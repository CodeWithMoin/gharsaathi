import Image from "next/image";

export function Brand({ compact = false, inverse = false, dense = false }: { compact?: boolean; inverse?: boolean; dense?: boolean }) {
  return <div className={`flex items-center ${dense ? "gap-2" : "gap-2.5"}`} aria-label="GharSaathi">
    <span className={`block overflow-hidden border border-accent-border bg-white ${dense ? "size-9 rounded-xl" : "size-10 rounded-[13px]"}`}><Image className="size-full scale-[1.08] object-cover" src="/gharsaathi-icon.png" alt="" width={48} height={48} priority /></span>
    {!compact && <strong className={`font-display font-semibold tracking-[-0.04em] ${dense ? "text-[16px]" : "text-[19px]"} ${inverse ? "text-on-inverse" : "text-ink"}`}>GharSaathi</strong>}
  </div>;
}
