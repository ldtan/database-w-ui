import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "primereact/button";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * The last line of defence: a render error caught and shown, instead of an app
 * that disappears.
 *
 * React unmounts the entire root when a render or commit throws and nothing
 * catches it — not the failing subtree, the whole thing. Until this existed the
 * result was a white page with no message, no way back, and nothing to report but
 * "it went blank"; the bug that prompted it was a tooltip moving a button in the
 * DOM (see `.is-inert` in themes.css), three components away from anything the
 * person had touched.
 *
 * It deliberately does **not** offer to retry the failed subtree in place. The
 * state that produced the error is still there, so re-rendering it usually throws
 * again; the honest offers are "go back to where you were" and "reload". Clearing
 * it is a remount, which the shell already does for free — `AppLayout` keys
 * `<main>` on the path, so navigating anywhere gives the boundary a clean start.
 *
 * This is a class because there is still no hook for it — `getDerivedStateFromError`
 * and `componentDidCatch` have no function-component equivalent.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Nothing collects these yet, so the console is the only record there is —
    // and the stack matters more than the message for this class of bug.
    console.error("Unhandled render error", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="flex flex-col items-center justify-center gap-4 p-8 text-center">
        <i
          className="pi pi-exclamation-triangle"
          style={{ fontSize: "2rem", color: "var(--text-muted)" }}
        />
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-medium">Something went wrong</h2>
          <p style={{ color: "var(--text-muted)" }}>
            This page stopped working. Nothing you were looking at has been
            changed.
          </p>
        </div>
        <pre
          className="max-w-xl overflow-x-auto rounded p-3 text-left text-xs"
          style={{ background: "var(--surface-2)", color: "var(--text-muted)" }}
        >
          {error.message}
        </pre>
        <div className="flex items-center gap-2">
          <Button
            label="Go back"
            icon="pi pi-arrow-left"
            outlined
            onClick={() => window.history.back()}
          />
          <Button
            label="Reload"
            icon="pi pi-refresh"
            onClick={() => window.location.reload()}
          />
        </div>
      </div>
    );
  }
}
