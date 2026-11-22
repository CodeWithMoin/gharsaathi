export function zeptoAddressSelection(
  data: { addresses: { id: string }[]; resolution: { default_address_id?: string | null }; selection_removed?: boolean },
  current: string,
): { selectedId: string; selectionRemoved: boolean };
