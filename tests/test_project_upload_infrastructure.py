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
        self.assertIn("POST /api/project-upload/files", self.template)
        self.assertIn("POST /api/project-upload/download", self.template)
        self.assertIn("POST /api/project-upload/delete", self.template)

    def test_storage_events_and_file_management_permissions_are_declared(self):
        self.assertIn("EventBridgeEnabled: true", self.template)
        self.assertIn("detail-type: [Object Created, Object Deleted]", self.template)
        self.assertIn("Principal: events.amazonaws.com", self.template)
        self.assertIn("- s3:GetObject", self.template)
        self.assertIn("- s3:DeleteObject", self.template)
        self.assertIn("Action: s3:ListBucket", self.template)

    def test_cloudformation_role_can_manage_project_event_rule(self):
        bootstrap = (Path(__file__).resolve().parents[1] / "infra/github-deploy-role.yaml").read_text(encoding="utf-8")
        self.assertIn("events:DescribeRule", bootstrap)
        self.assertIn("events:PutRule", bootstrap)
        self.assertIn("events:PutTargets", bootstrap)
        self.assertIn("events:RemoveTargets", bootstrap)
        self.assertIn("events:DeleteRule", bootstrap)
        self.assertIn("rule/${ProductionStackName}-*", bootstrap)


if __name__ == "__main__":
    unittest.main()
