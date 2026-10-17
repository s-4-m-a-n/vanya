import { Leaf } from "lucide-react";
import { useEffect, useState } from "react";

import { getDb } from "../db";

/** Server photo, falling back to the locally stored thumbnail when offline. */
export function Photo({ src, clientUuid, className }: {
  src?: string;
  clientUuid: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(!src);
  const [thumbnail, setThumbnail] = useState<string | undefined>();

  useEffect(() => {
    if (!failed) return;
    let cancelled = false;
    getDb()
      .then((db) => db.get("thumbnails", clientUuid))
      .then((value) => !cancelled && setThumbnail(value));
    return () => {
      cancelled = true;
    };
  }, [failed, clientUuid]);

  if (!failed && src) {
    return <img className={className} src={src} alt="" onError={() => setFailed(true)} />;
  }
  return thumbnail ? (
    <img className={className} src={thumbnail} alt="" />
  ) : (
    <div className={`${className ?? ""} placeholder`} aria-hidden>
      <Leaf size={28} />
    </div>
  );
}
