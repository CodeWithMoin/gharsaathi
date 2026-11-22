/** Owner-only deployments: the backend answers 403 `private_deployment` to anyone not on its allow-list. */

export const PRIVATE_DEPLOYMENT_CODE = "private_deployment";
export const PRIVATE_DEPLOYMENT_EVENT = "gharsaathi:private-deployment";
export const PRIVATE_DEPLOYMENT_MESSAGE = "This deployment is private.";

/** True only for the backend's explicit private-deployment refusal, never for other 403s (e.g. terms not accepted). */
export function isPrivateDeploymentRefusal(status, payload) {
  return status === 403 && !!payload && typeof payload === "object" && payload.code === PRIVATE_DEPLOYMENT_CODE;
}
