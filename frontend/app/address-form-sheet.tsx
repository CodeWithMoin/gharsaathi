"use client";

import { SpinnerGap } from "@phosphor-icons/react";
import { useId, useState } from "react";
import { InputDialog } from "./ui/input-dialog";
import { ZeptoAddressForm, type ZeptoAddressList, type ZeptoAddressPrefillRequest, type ZeptoPrefillResult } from "./zepto-address-form";

/** The one Add delivery address popup, for voice, typed chat and the manual button alike. */
export function AddressFormSheet({
  open,
  prefillRequest,
  onAdded,
  onClose,
  onPrefillApplied,
}: {
  open: boolean;
  prefillRequest: ZeptoAddressPrefillRequest | null;
  onAdded: (result: ZeptoAddressList) => void;
  onClose: () => void;
  onPrefillApplied?: (result: ZeptoPrefillResult) => void;
}) {
  const formId = useId();
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);

  return (
    <InputDialog
      open={open}
      title="Add delivery address"
      description="Saved to your connected Zepto account after you tap Save."
      dirty={dirty}
      busy={busy}
      discardMessage="Discard this address? What you entered will be lost."
      closeLabel="Close address form"
      onClose={() => { setDirty(false); setBusy(false); onClose(); }}
      footer={({ requestClose }) => (
        <div className="flex items-center gap-3">
          <button type="button" onClick={() => requestClose("cancel")} disabled={busy} className="rounded-full px-4 py-3 text-sm font-semibold text-[#6f5b50] transition hover:bg-[#f3e7dd] disabled:opacity-40">Cancel</button>
          <button type="submit" form={formId} disabled={busy} className="ml-auto flex flex-1 items-center justify-center gap-2 rounded-full bg-[#6f359f] px-5 py-3 text-sm font-semibold text-white transition hover:bg-[#5d2c86] disabled:opacity-45 sm:flex-none">{busy ? <><SpinnerGap size={17} className="animate-spin" /> Saving to Zepto…</> : "Save address"}</button>
        </div>
      )}
    >
      <ZeptoAddressForm
        formId={formId}
        prefillRequest={prefillRequest}
        onAdded={(result) => { setDirty(false); onAdded(result); }}
        onPrefillApplied={onPrefillApplied}
        onDirtyChange={setDirty}
        onBusyChange={setBusy}
      />
    </InputDialog>
  );
}
