import {
  createContext,
  useContext,
  useMemo,
  useRef,
  type ReactNode,
} from "react";
import { Toast } from "primereact/toast";

interface ToastApi {
  success: (summary: string, detail?: string) => void;
  error: (summary: string, detail?: string) => void;
  info: (summary: string, detail?: string) => void;
  warn: (summary: string, detail?: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

// App-wide transient notifications. Mount once near the root; any component
// calls useToast() to show success/error/info/warn messages.
export function ToastProvider({ children }: { children: ReactNode }) {
  const ref = useRef<Toast>(null);

  const api = useMemo<ToastApi>(
    () => ({
      success: (summary, detail) =>
        ref.current?.show({ severity: "success", summary, detail, life: 3000 }),
      error: (summary, detail) =>
        ref.current?.show({ severity: "error", summary, detail, life: 6000 }),
      info: (summary, detail) =>
        ref.current?.show({ severity: "info", summary, detail, life: 3000 }),
      warn: (summary, detail) =>
        ref.current?.show({ severity: "warn", summary, detail, life: 5000 }),
    }),
    [],
  );

  return (
    <ToastContext.Provider value={api}>
      <Toast ref={ref} position="top-right" />
      {children}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within a ToastProvider");
  return ctx;
}
