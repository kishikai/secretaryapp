import { useRef } from "react";
import { newIdempotencyKey } from "../api.js";

// One Idempotency-Key per create action. A tap again after a dropped connection reuses the same key
// (so the server doesn't add a second copy); it changes only after the action succeeded or its inputs changed.
//   const idem = useIdempotency();
//   await api(path, { method: "POST", body, idempotencyKey: idem.keyFor(body) });
//   idem.done();
export function useIdempotency() {
  const ref = useRef(null);
  if (ref.current === null) ref.current = { key: newIdempotencyKey(), sig: null };
  return {
    keyFor(inputs) {
      const sig = JSON.stringify(inputs);
      if (ref.current.sig !== null && ref.current.sig !== sig) ref.current.key = newIdempotencyKey();
      ref.current.sig = sig;
      return ref.current.key;
    },
    done() {
      ref.current = { key: newIdempotencyKey(), sig: null };
    },
  };
}
