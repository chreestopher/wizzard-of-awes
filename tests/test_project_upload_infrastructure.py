from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProjectUploadInfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "infra" / "template.yaml").read_text(encoding="utf-8")

    def test_durable_uploads_use_a_separate_retained_bucket(self):
        section = self.template.split("  ProjectUploadBucket:", 1)[1].split("  ProjectUploadBucketPolicy:", 1)[0]
        self.assertIn("DeletionPolicy: Retain", section)
        self.assertIn("UpdateReplacePolicy: Retain", section)
        self.assertNotIn("ExpirationInDays", section)
        self.assertNotIn("NoncurrentVersionExpiration", section)

    def test_bucket_is_private_and_api_can_only_write_project_prefix(self):
        self.assertIn("PROJECT_UPLOAD_BUCKET: !Ref ProjectUploadBucket", self.template)
        self.assertIn("Resource: !Sub '${ProjectUploadBucket.Arn}/projects/*'", self.template)
        section = self.template.split("  ProjectUploadBucket:", 1)[1].split("  ProjectUploadBucketPolicy:", 1)[0]
        self.assertIn("BlockPublicAcls: true", section)
        self.assertIn("RestrictPublicBuckets: true", section)

    def test_access_code_is_noecho_and_both_routes_are_declared(self):
        parameter = self.template.split("  ProjectUploadCodeHash:", 1)[1].split("  LambdaArtifactBucket:", 1)[0]
        self.assertIn("NoEcho: true", parameter)
        self.assertIn("POST /api/project-upload/access", self.template)
        self.assertIn("POST /api/project-upload/grants", self.template)


if __name__ == "__main__":
    unittest.main()
