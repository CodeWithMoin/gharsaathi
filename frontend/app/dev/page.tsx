import { notFound } from "next/navigation";

import { DevInspector } from "./dev-inspector";

export const metadata = { title: "GharSaathi · dev inspector", robots: { index: false, follow: false } };

// Local development only: a production build (and therefore the AWS image) never renders this.
export default function DevPage() {
  if (process.env.NODE_ENV === "production") notFound();
  return <DevInspector />;
}
