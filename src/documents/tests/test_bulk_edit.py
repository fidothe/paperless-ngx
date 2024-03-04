import os
import shutil
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from django.test import TestCase
from guardian.shortcuts import assign_perm
from guardian.shortcuts import get_groups_with_perms
from guardian.shortcuts import get_users_with_perms

from documents.bulk_edit import merge
from documents.bulk_edit import rotate
from documents.bulk_edit import set_permissions
from documents.bulk_edit import split
from documents.models import Document
from documents.tests.utils import DirectoriesMixin


class TestBulkEditPermissions(DirectoriesMixin, TestCase):
    def setUp(self):
        super().setUp()

        self.doc1 = Document.objects.create(checksum="A", title="A")
        self.doc2 = Document.objects.create(checksum="B", title="B")
        self.doc3 = Document.objects.create(checksum="C", title="C")

        self.owner = User.objects.create(username="test_owner")
        self.user1 = User.objects.create(username="user1")
        self.user2 = User.objects.create(username="user2")
        self.group1 = Group.objects.create(name="group1")
        self.group2 = Group.objects.create(name="group2")

    @mock.patch("documents.tasks.bulk_update_documents.delay")
    def test_set_permissions(self, m):
        doc_ids = [self.doc1.id, self.doc2.id, self.doc3.id]

        assign_perm("view_document", self.group1, self.doc1)

        permissions = {
            "view": {
                "users": [self.user1.id, self.user2.id],
                "groups": [self.group2.id],
            },
            "change": {
                "users": [self.user1.id],
                "groups": [self.group2.id],
            },
        }

        set_permissions(
            doc_ids,
            set_permissions=permissions,
            owner=self.owner,
            merge=False,
        )
        m.assert_called_once()

        self.assertEqual(Document.objects.filter(owner=self.owner).count(), 3)
        self.assertEqual(Document.objects.filter(id__in=doc_ids).count(), 3)

        users_with_perms = get_users_with_perms(
            self.doc1,
        )
        self.assertEqual(users_with_perms.count(), 2)

        # group1 should be replaced by group2
        groups_with_perms = get_groups_with_perms(
            self.doc1,
        )
        self.assertEqual(groups_with_perms.count(), 1)

    @mock.patch("documents.tasks.bulk_update_documents.delay")
    def test_set_permissions_merge(self, m):
        doc_ids = [self.doc1.id, self.doc2.id, self.doc3.id]

        self.doc1.owner = self.user1
        self.doc1.save()

        assign_perm("view_document", self.user1, self.doc1)
        assign_perm("view_document", self.group1, self.doc1)

        permissions = {
            "view": {
                "users": [self.user2.id],
                "groups": [self.group2.id],
            },
            "change": {
                "users": [self.user2.id],
                "groups": [self.group2.id],
            },
        }
        set_permissions(
            doc_ids,
            set_permissions=permissions,
            owner=self.owner,
            merge=True,
        )
        m.assert_called_once()

        # when merge is true owner doesn't get replaced if its not empty
        self.assertEqual(Document.objects.filter(owner=self.owner).count(), 2)
        self.assertEqual(Document.objects.filter(id__in=doc_ids).count(), 3)

        # merge of user1 which was pre-existing and user2
        users_with_perms = get_users_with_perms(
            self.doc1,
        )
        self.assertEqual(users_with_perms.count(), 2)

        # group1 should be merged by group2
        groups_with_perms = get_groups_with_perms(
            self.doc1,
        )
        self.assertEqual(groups_with_perms.count(), 2)


class TestPDFActions(DirectoriesMixin, TestCase):
    def setUp(self):
        super().setUp()
        sample1 = os.path.join(self.dirs.scratch_dir, "sample.pdf")
        shutil.copy(
            os.path.join(
                os.path.dirname(__file__),
                "samples",
                "documents",
                "originals",
                "0000001.pdf",
            ),
            sample1,
        )
        sample1_archive = os.path.join(self.dirs.archive_dir, "sample_archive.pdf")
        shutil.copy(
            os.path.join(
                os.path.dirname(__file__),
                "samples",
                "documents",
                "originals",
                "0000001.pdf",
            ),
            sample1_archive,
        )
        sample2 = os.path.join(self.dirs.scratch_dir, "sample2.pdf")
        shutil.copy(
            os.path.join(
                os.path.dirname(__file__),
                "samples",
                "documents",
                "originals",
                "0000002.pdf",
            ),
            sample2,
        )
        sample2_archive = os.path.join(self.dirs.archive_dir, "sample2_archive.pdf")
        shutil.copy(
            os.path.join(
                os.path.dirname(__file__),
                "samples",
                "documents",
                "originals",
                "0000002.pdf",
            ),
            sample2_archive,
        )
        sample3 = os.path.join(self.dirs.scratch_dir, "sample3.pdf")
        shutil.copy(
            os.path.join(
                os.path.dirname(__file__),
                "samples",
                "documents",
                "originals",
                "0000003.pdf",
            ),
            sample3,
        )
        self.doc1 = Document.objects.create(checksum="A", title="A", filename=sample1)
        self.doc1.archive_filename = sample1_archive
        self.doc1.save()
        self.doc2 = Document.objects.create(checksum="B", title="B", filename=sample2)
        self.doc2.archive_filename = sample2_archive
        self.doc2.save()
        self.doc3 = Document.objects.create(checksum="C", title="C", filename=sample3)
        img_doc = os.path.join(self.dirs.scratch_dir, "sample_image.jpg")
        shutil.copy(
            os.path.join(
                os.path.dirname(__file__),
                "samples",
                "simple.jpg",
            ),
            img_doc,
        )
        self.img_doc = Document.objects.create(
            checksum="D",
            title="D",
            filename=img_doc,
        )

    @mock.patch("documents.tasks.consume_file.delay")
    def test_merge(self, mock_consume_file):
        """
        GIVEN:
        - Existing documents
        WHEN:
        - Merge action is called with 3 documents
        THEN:
        - Consume file should be called
        """
        doc_ids = [self.doc1.id, self.doc2.id, self.doc3.id]
        metadata_document_id = self.doc1.id

        result = merge(doc_ids)

        expected_filename = (
            f"{'_'.join([str(doc_id) for doc_id in doc_ids])[:100]}_merged.pdf"
        )

        mock_consume_file.assert_called()
        consume_file_args, _ = mock_consume_file.call_args
        self.assertEqual(
            Path(consume_file_args[0].original_file).name,
            expected_filename,
        )
        self.assertEqual(consume_file_args[1].title, None)

        # With metadata_document_id overrides
        result = merge(doc_ids, metadata_document_id=metadata_document_id)
        consume_file_args, _ = mock_consume_file.call_args
        self.assertEqual(consume_file_args[1].title, "A (merged)")

        self.assertEqual(result, "OK")

    @mock.patch("documents.tasks.consume_file.delay")
    @mock.patch("pikepdf.open")
    def test_merge_with_errors(self, mock_open_pdf, mock_consume_file):
        """
        GIVEN:
        - Existing documents
        WHEN:
        - Merge action is called with 2 documents
        - Error occurs when opening both files
        THEN:
        - Consume file should not be called
        """
        mock_open_pdf.side_effect = Exception("Error opening PDF")
        doc_ids = [self.doc2.id, self.doc3.id]

        with self.assertLogs("paperless.bulk_edit", level="ERROR") as cm:
            merge(doc_ids)
            error_str = cm.output[0]
            expected_str = (
                "Error merging document 2, it will not be included in the merge"
            )
            self.assertIn(expected_str, error_str)

        mock_consume_file.assert_not_called()

    @mock.patch("documents.tasks.consume_file.delay")
    def test_split(self, mock_consume_file):
        """
        GIVEN:
        - Existing documents
        WHEN:
        - Split action is called with 1 document and 2 pages
        THEN:
        - Consume file should be called twice
        """
        doc_ids = [self.doc2.id]
        pages = [[1, 2], [3]]
        result = split(doc_ids, pages)
        self.assertEqual(mock_consume_file.call_count, 2)
        consume_file_args, _ = mock_consume_file.call_args
        self.assertEqual(consume_file_args[1].title, "B (split 2)")

        self.assertEqual(result, "OK")

    @mock.patch("documents.tasks.consume_file.delay")
    @mock.patch("pikepdf.Pdf.save")
    def test_split_with_errors(self, mock_save_pdf, mock_consume_file):
        """
        GIVEN:
        - Existing documents
        WHEN:
        - Split action is called with 1 document and 2 page groups
        - Error occurs when saving the files
        THEN:
        - Consume file should not be called
        """
        mock_save_pdf.side_effect = Exception("Error saving PDF")
        doc_ids = [self.doc2.id]
        pages = [[1, 2], [3]]

        with self.assertLogs("paperless.bulk_edit", level="ERROR") as cm:
            split(doc_ids, pages)
            error_str = cm.output[0]
            expected_str = "Error splitting document 2"
            self.assertIn(expected_str, error_str)

        mock_consume_file.assert_not_called()

    @mock.patch("documents.tasks.bulk_update_documents.delay")
    @mock.patch("documents.tasks.update_document_archive_file.delay")
    def test_rotate(self, mock_update_document, mock_update_documents):
        """
        GIVEN:
        - Existing documents
        WHEN:
        - Rotate action is called with 2 documents
        THEN:
        - Rotate action should be called twice
        """
        doc_ids = [self.doc1.id, self.doc2.id]
        result = rotate(doc_ids, 90)
        self.assertEqual(mock_update_document.call_count, 2)
        mock_update_documents.assert_called_once()

        self.assertEqual(result, "OK")

    @mock.patch("documents.tasks.bulk_update_documents.delay")
    @mock.patch("documents.tasks.update_document_archive_file.delay")
    @mock.patch("pikepdf.Pdf.save")
    def test_rotate_with_error(
        self,
        mock_pdf_save,
        mock_update_archive_file,
        mock_update_documents,
    ):
        """
        GIVEN:
        - Existing documents
        WHEN:
        - Rotate action is called with 2 documents
        - PikePDF raises an error
        THEN:
        - Rotate action should be called 0 times
        """
        mock_pdf_save.side_effect = Exception("Error saving PDF")
        doc_ids = [self.doc2.id, self.doc3.id]

        with self.assertLogs("paperless.bulk_edit", level="ERROR") as cm:
            rotate(doc_ids, 90)
            error_str = cm.output[0]
            expected_str = "Error rotating document 2"
            self.assertIn(expected_str, error_str)
            mock_update_archive_file.assert_not_called()
