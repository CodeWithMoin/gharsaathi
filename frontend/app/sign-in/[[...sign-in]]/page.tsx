import { SignIn } from "@clerk/nextjs";
import { Suspense } from "react";
import { AuthShell, clerkAppearance } from "../../auth-shell";

export default function SignInPage() {
  return (
    <AuthShell>
      <Suspense>
        <SignIn appearance={clerkAppearance} />
      </Suspense>
    </AuthShell>
  );
}
