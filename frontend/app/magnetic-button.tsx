"use client";

import { motion, useMotionValue, useSpring } from "framer-motion";
import type { MouseEvent, ReactNode } from "react";

export function MagneticButton({ children, className, onClick }: { children: ReactNode; className?: string; onClick?: () => void }) {
  const rawX = useMotionValue(0); const rawY = useMotionValue(0);
  const x = useSpring(rawX, { stiffness: 100, damping: 20 });
  const y = useSpring(rawY, { stiffness: 100, damping: 20 });
  function move(event: MouseEvent<HTMLButtonElement>) { const box = event.currentTarget.getBoundingClientRect(); rawX.set((event.clientX - box.left - box.width / 2) * .12); rawY.set((event.clientY - box.top - box.height / 2) * .12); }
  function reset() { rawX.set(0); rawY.set(0); }
  return <motion.button style={{ x, y }} whileTap={{ scale: .98 }} onMouseMove={move} onMouseLeave={reset} onClick={onClick} className={className}>{children}</motion.button>;
}
