import { Camera, ChevronRight, Footprints, ImagePlus, Loader2, Lock, MapPin, MapPinOff, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { getDb } from "../db";
import { getPosition, makeThumbnail, newUuid, type Fix } from "../device";
import { useT } from "../i18n";
import { syncManager, useOnline } from "../syncManager";
import { HABITATS, type Habitat, type QueuedCapture, type User } from "../types";

interface Props {
  user: User;
  /** Sightings waiting for the observer to confirm a species. */
  needsId: number;
  /** No sightings yet: explain how it works. */
  firstTime: boolean;
  onOpenNeedsId: () => void;
  /** Called after saving; observationId is set when it was uploaded straight away. */
  onSaved: (observationId?: string) => void;
}

type Source = "camera" | "gallery";

export function CaptureScreen({ user, needsId, firstTime, onOpenNeedsId, onSaved }: Props) {
  const t = useT();
  const online = useOnline();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [source, setSource] = useState<Source>("camera");
  const [takenHereNow, setTakenHereNow] = useState(true);
  const [fix, setFix] = useState<Fix | null>(null);
  const [locating, setLocating] = useState(false);
  const [habitat, setHabitat] = useState<Habitat | "">("");
  const [notes, setNotes] = useState("");
  const [obscure, setObscure] = useState(false);
  const [phase, setPhase] = useState<"idle" | "saving" | "identifying">("idle");
  const capturedAt = useRef<string>("");
  const fixPromise = useRef<Promise<Fix | null> | null>(null);

  useEffect(() => () => void (preview && URL.revokeObjectURL(preview)), [preview]);

  const useDeviceContext = source === "camera" || takenHereNow;

  function locate() {
    setLocating(true);
    fixPromise.current = getPosition().then((result) => {
      setFix(result);
      setLocating(false);
      return result;
    });
  }

  function pick(selected: File | undefined, from: Source) {
    if (!selected) return;
    setFile(selected);
    setPreview(URL.createObjectURL(selected));
    setSource(from);
    setTakenHereNow(from === "camera");
    capturedAt.current = new Date().toISOString();
    setFix(null);
    fixPromise.current = null;
    if (from === "camera") locate();
  }

  function reset() {
    setFile(null);
    setPreview(null);
    setFix(null);
    setHabitat("");
    setNotes("");
    setObscure(false);
    fixPromise.current = null;
  }

  async function save() {
    if (!file) return;
    setPhase("saving");
    const position = useDeviceContext ? await (fixPromise.current ?? Promise.resolve(fix)) : null;
    const capture: QueuedCapture = {
      clientUuid: newUuid(),
      photo: file,
      photoName: file.name || "photo.jpg",
      ...(position && {
        latitude: position.latitude,
        longitude: position.longitude,
        accuracyM: position.accuracyM,
      }),
      // Gallery photos without "here, now" rely on the photo's EXIF time on the server.
      ...(useDeviceContext && { observedAt: capturedAt.current }),
      ...(habitat && { habitat }),
      ...(notes.trim() && { notes: notes.trim() }),
      ...(obscure && { obscureLocation: true }),
      state: "queued",
      attempts: 0,
      createdAt: new Date().toISOString(),
    };
    const db = await getDb();
    await db.put("queue", capture);
    const thumbnail = await makeThumbnail(file);
    if (thumbnail) await db.put("thumbnails", thumbnail, capture.clientUuid);
    syncManager.notify();

    if (!navigator.onLine) {
      reset();
      setPhase("idle");
      alert(t("savedOffline"));
      onSaved();
      return;
    }
    setPhase("identifying");
    // A sync already in flight may have read the queue before this capture was added;
    // if it didn't upload it, the next run will.
    const isThis = (o: { client_uuid: string }) => o.client_uuid === capture.clientUuid;
    let uploaded = (await syncManager.run())?.uploaded.find(isThis);
    if (!uploaded) uploaded = (await syncManager.run())?.uploaded.find(isThis);
    reset();
    setPhase("idle");
    onSaved(uploaded?.id);
  }

  if (!file) {
    const firstName = user.display_name.split(" ")[0];
    return (
      <main className="screen stack">
        <div className="hello">
          <h1>{t("greeting", { name: firstName })}</h1>
          <p className="muted">{t("captureLead")}</p>
        </div>
        {needsId > 0 && (
          <button className="banner" onClick={onOpenNeedsId}>
            <Sparkles size={20} className="lead" aria-hidden />
            <span className="grow">
              {needsId === 1 ? t("needsIdBannerOne") : t("needsIdBanner", { n: needsId })}
            </span>
            <ChevronRight size={18} aria-hidden />
          </button>
        )}
        <section className="card shutter-zone">
          <label className="shutter" aria-label={t("takePhoto")}>
            <Camera size={52} strokeWidth={1.75} aria-hidden />
            <input
              type="file"
              accept="image/*"
              capture="environment"
              hidden
              onChange={(e) => pick(e.target.files?.[0], "camera")}
            />
          </label>
          <span className="shutter-label">{t("takePhoto")}</span>
          <label className="gallery-button">
            <ImagePlus size={18} aria-hidden /> {t("chooseGallery")}
            <input
              type="file"
              accept="image/*"
              hidden
              onChange={(e) => pick(e.target.files?.[0], "gallery")}
            />
          </label>
        </section>
        {firstTime && (
          <section className="card stack">
            <h2>{t("howTitle")}</h2>
            <ol className="steps">
              <li>{t("how1")}</li>
              <li>{t("how2")}</li>
              <li>{t("how3")}</li>
            </ol>
          </section>
        )}
        <p className="tip">
          <Footprints size={18} aria-hidden />
          {t("safetyTip")}
        </p>
      </main>
    );
  }

  const locationPill = !useDeviceContext ? (
    <span className="pill neutral"><MapPin size={15} aria-hidden /> {t("usingPhotoData")}</span>
  ) : locating ? (
    <span className="pill neutral"><Loader2 size={15} className="spin" aria-hidden /> {t("locating")}</span>
  ) : fix ? (
    <span className="pill"><MapPin size={15} aria-hidden /> {t("locationAccuracy", { m: fix.accuracyM })}</span>
  ) : (
    <span className="pill warn"><MapPinOff size={15} aria-hidden /> {t("locationUnavailable")}</span>
  );

  return (
    <main className="screen stack">
      {preview && <img className="preview" src={preview} alt="" />}
      {locationPill}

      {source === "gallery" && (
        <label className="switch-row">
          <input
            type="checkbox"
            className="switch"
            checked={takenHereNow}
            onChange={(e) => {
              setTakenHereNow(e.target.checked);
              if (e.target.checked && !fixPromise.current) locate();
            }}
          />
          <span className="grow">{t("takenHereNow")}</span>
        </label>
      )}

      <section className="card stack">
        <div className="stack tight">
          <span className="field-label">{t("habitat")}</span>
          <div className="chip-row" role="radiogroup" aria-label={t("habitat")}>
            {(["", ...HABITATS] as const).map((h) => (
              <label key={h || "auto"} className={`chip-toggle ${habitat === h ? "selected" : ""}`}>
                <input
                  type="radio"
                  name="habitat"
                  checked={habitat === h}
                  onChange={() => setHabitat(h)}
                />
                {h ? t(`habitat_${h}`) : t("autoHabitat")}
              </label>
            ))}
          </div>
        </div>
        <label>
          {t("notes")}
          <textarea value={notes} maxLength={1000} rows={2} onChange={(e) => setNotes(e.target.value)} />
        </label>
        <label className="switch-row">
          <Lock size={18} className="muted" aria-hidden />
          <span className="grow">
            {t("hideExactLocation")}
            <span className="muted small">{t("hideExactLocationHint")}</span>
          </span>
          <input type="checkbox" className="switch" checked={obscure} onChange={(e) => setObscure(e.target.checked)} />
        </label>
      </section>

      <div className="action-bar">
        <button onClick={reset} disabled={phase !== "idle"}>
          {t("cancel")}
        </button>
        <button className="primary" onClick={save} disabled={phase !== "idle"}>
          {phase !== "idle" && <Loader2 size={18} className="spin" aria-hidden />}
          {phase === "identifying"
            ? t("identifying")
            : phase === "saving" && locating
              ? t("locating")
              : t("save")}
        </button>
      </div>
      {!online && <p className="muted small">{t("savedOffline")}</p>}
    </main>
  );
}
