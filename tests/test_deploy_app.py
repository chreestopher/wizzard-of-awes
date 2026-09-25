import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("deploy_app", Path(__file__).resolve().parents[1] / "scripts/deploy-app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

class AppDeployTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"STACK_NAME": "test-stack", "ARTIFACT_BUCKET": "artifacts",
                                     "CLOUDFORMATION_ROLE_ARN": "test-role", "AWS_REGION": "us-east-1"})
        env.start()
        self.addCleanup(env.stop)
        self.cf, self.s3 = MagicMock(), MagicMock()
        self.cf.create_change_set.return_value = {"Id": "change"}
        self.cf.describe_change_set.return_value = {"Status": "CREATE_COMPLETE"}
        self.cf.describe_stacks.return_value = {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]}

    def test_deployment_preserves_private_parameters(self):
        app.deploy_backend(self.cf, self.s3)
        args = self.cf.create_change_set.call_args.kwargs
        self.assertEqual(args["ChangeSetType"], "UPDATE")
        self.assertIn({"ParameterKey": "NotificationEmail", "UsePreviousValue": True}, args["Parameters"])
        self.assertEqual(args["RoleARN"], "test-role")
        self.cf.execute_change_set.assert_called_once()

    def test_rollback_fails_release(self):
        self.cf.describe_stacks.return_value = {"Stacks": [{"StackStatus": "UPDATE_ROLLBACK_IN_PROGRESS"}]}
        with self.assertRaises(RuntimeError):
            app.deploy_backend(self.cf, self.s3)

    def test_no_changes_is_success(self):
        self.cf.describe_change_set.return_value = {"Status": "FAILED", "StatusReason": "The submitted information didn't contain changes."}
        app.deploy_backend(self.cf, self.s3)
        self.cf.execute_change_set.assert_not_called()

    def test_failed_backend_does_not_publish_site(self):
        with patch.object(app.boto3, "Session"), patch.object(app, "deploy_backend", side_effect=RuntimeError), patch.object(app.importlib.util, "spec_from_file_location") as publish:
            with self.assertRaises(RuntimeError):
                app.main()
            publish.assert_not_called()

    def test_package_is_reproducible(self):
        self.assertEqual(app.package_backend(), app.package_backend())
