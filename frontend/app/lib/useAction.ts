/**
 * The state of a button that does something: busy while it runs, the error
 * if it failed. Replaces the try / setBusy / catch / setError / finally
 * blocks every panel would otherwise repeat.
 *
 *   const save = useAction();
 *   <Button disabled={save.busy} onClick={() => save.run(() => api.save(x), refresh)}>
 *   {save.error && <Alert>{save.error}</Alert>}
 */
import { useCallback, useState } from "react";

import { errorText } from "~/api/http";

export interface Action {
  busy: boolean;
  error: string | null;
  setError: (error: string | null) => void;
  /** Runs `work`, then `after` with its result; true when both succeeded. */
  run: <T>(work: () => Promise<T>, after?: (result: T) => unknown) => Promise<boolean>;
}

export function useAction(): Action {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async <T,>(work: () => Promise<T>, after?: (result: T) => unknown) => {
    setBusy(true);
    setError(null);
    try {
      const result = await work();
      await after?.(result);
      return true;
    } catch (err) {
      setError(errorText(err));
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  return { busy, error, setError, run };
}
