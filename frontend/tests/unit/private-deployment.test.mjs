import assert from "node:assert/strict";
import test from "node:test";
import { PRIVATE_DEPLOYMENT_CODE, isPrivateDeploymentRefusal } from "../../app/private-deployment.mjs";

test("only the backend's private-deployment 403 shows the private screen", () => {
  assert.equal(isPrivateDeploymentRefusal(403, { detail: "This deployment is private.", code: PRIVATE_DEPLOYMENT_CODE }), true);
});

test("other refusals keep their normal handling", () => {
  assert.equal(isPrivateDeploymentRefusal(403, { detail: "Accept the terms first.", code: "legal_consent_required" }), false);
  assert.equal(isPrivateDeploymentRefusal(403, null), false);
  assert.equal(isPrivateDeploymentRefusal(403, "private_deployment"), false);
  assert.equal(isPrivateDeploymentRefusal(401, { code: PRIVATE_DEPLOYMENT_CODE }), false);
});
