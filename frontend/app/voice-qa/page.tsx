import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import Link from "next/link";

import mockManifest from "../../public/voice-qa/manifest.json";
import styles from "./voice-qa.module.css";

type VoiceManifest = typeof mockManifest & { model?: string };


function loadRealtimeManifest(): VoiceManifest | null {
  const path = join(process.cwd(), "public", "voice-qa-openai", "manifest.json");
  if (!existsSync(path)) {
    return null;
  }
  return JSON.parse(readFileSync(path, "utf8")) as VoiceManifest;
}


export default function VoiceQaPage() {
  const realtimeManifest = loadRealtimeManifest();

  return (
    <main className={styles.page}>
      <nav className={styles.nav}>
        <Link href="/">← Back to GharSaathi</Link>
        <span>Internal voice QA</span>
      </nav>

      <header className={styles.hero}>
        <p className={styles.eyebrow}>GHARSAATHI · MOCK VOICE LAB</p>
        <h1>Does the shopping voice sound natural?</h1>
        <p>
          Compare three phrasings for each mock Instamart or Zepto title. Listen
          for Hindi pronunciation, feminine grammar, pacing, and repetition.
        </p>
        <div className={styles.warning}>
          {realtimeManifest
            ? `${realtimeManifest.model ?? "OpenAI Realtime"} · ${realtimeManifest.voice}: ${realtimeManifest.disclaimer}`
            : "OpenAI Realtime samples have not been generated in this checkout yet."}
        </div>
      </header>

      {realtimeManifest ? (
        <VoiceLab manifest={realtimeManifest} label="OpenAI Realtime" />
      ) : null}

      <VoiceLab manifest={mockManifest} label="Local mock TTS" />
    </main>
  );
}


function VoiceLab({
  manifest,
  label,
}: {
  manifest: VoiceManifest;
  label: string;
}) {
  return (
    <section className={styles.lab}>
      <div className={styles.labHeading}>
        <p className={styles.eyebrow}>{label}</p>
        <h2>{manifest.voice}</h2>
        <p>{manifest.disclaimer}</p>
      </div>

      <div className={styles.metrics} aria-label={`${label} diversity metrics`}>
        <Metric label="Mock titles" value={manifest.metrics.case_count} />
        <Metric label="Audio samples" value={manifest.metrics.utterance_count} />
        <Metric
          label="Unique responses"
          value={`${Math.round(manifest.metrics.unique_response_ratio * 100)}%`}
        />
        <Metric
          label="Unique openings"
          value={`${Math.round(manifest.metrics.unique_opener_ratio * 100)}%`}
        />
        <Metric label="Distinct bigrams" value={manifest.metrics.distinct_2} />
      </div>

      <div className={styles.catalogue}>
        {manifest.cases.map((item) => (
          <article className={styles.case} key={item.id}>
            <div className={styles.caseHeading}>
              <span>{item.provider}</span>
              <h2>{item.title}</h2>
            </div>
            <div className={styles.variants}>
              {item.variants.map((variant) => (
                <div className={styles.variant} key={variant.id}>
                  <div className={styles.variantTopline}>
                    <strong>Take {variant.id}</strong>
                    <small>{variant.duration_seconds.toFixed(1)} sec</small>
                  </div>
                  <p lang="hi-Latn">{variant.text}</p>
                  <audio controls preload="none" src={variant.audio}>
                    Your browser does not support audio playback.
                  </audio>
                </div>
              ))}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}


function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}
