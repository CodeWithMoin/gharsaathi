import Image from "next/image";

export function Brand({ compact = false, inverse = false }: { compact?: boolean; inverse?: boolean }) {
  return <div className="flex items-center gap-2.5" aria-label="GharSaathi">
    <span className="block size-10 overflow-hidden rounded-[13px] border border-[#e7cbb8] bg-white"><Image className="size-full scale-[1.08] object-cover" src="/gharsaathi-icon.png" alt="" width={48} height={48} priority /></span>
    {!compact && <strong className={`font-display text-[19px] font-semibold tracking-[-0.04em] ${inverse ? "text-[#fff9f4]" : "text-[#35241c]"}`}>GharSaathi</strong>}
  </div>;
}
