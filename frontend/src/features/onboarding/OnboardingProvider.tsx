import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useCurrentUser } from "../../lib/auth";
import { useMyPerson } from "../../lib/people";
import { useToast } from "../../lib/toast";
import { DESKTOP_SCREEN, useMediaQuery } from "../../lib/useMediaQuery";
import {
  readOnboarding,
  writeOnboarding,
  type OnboardingState,
} from "./onboardingState";
import { TOURS, type TourId } from "./tours";
import { TourOverlay } from "./TourOverlay";
import { WelcomeDialog } from "./WelcomeDialog";

/**
 * Who has been shown what, and the running of a tour.
 *
 * It owns three things that have to agree with each other: the record of what
 * this account has already seen, the welcome that shows only while that record
 * says "nothing", and the step a tour is on. Splitting them left the welcome
 * able to reappear behind a tour it had just started.
 *
 * Mounted inside the authenticated shell, because everything here is keyed by
 * username and half of it navigates.
 */

interface OnboardingApi {
  startTour: (id: TourId) => void;
  hasSeen: (id: TourId) => boolean;
  /** The tour running right now, so the help button can close itself and get
   *  out of the way of the one it just started. */
  runningTour: TourId | null;
}

const OnboardingContext = createContext<OnboardingApi | null>(null);

export function OnboardingProvider({ children }: { children: ReactNode }) {
  const me = useCurrentUser();
  const person = useMyPerson();
  const toast = useToast();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const wide = useMediaQuery(DESKTOP_SCREEN);

  // Null until the account is known: the record is per-username, and guessing
  // before it resolves would offer the welcome to somebody who has answered it.
  const [state, setState] = useState<OnboardingState | null>(null);
  const [running, setRunning] = useState<{ id: TourId; index: number } | null>(
    null,
  );

  const username = me?.username;
  useEffect(() => {
    setState(username ? readOnboarding(username) : null);
  }, [username]);

  const save = useCallback(
    (next: OnboardingState) => {
      setState(next);
      if (username) writeOnboarding(username, next);
    },
    [username],
  );

  const tour = running ? TOURS[running.id] : null;

  // The rail's steps and the burger's step are the same step at two widths, so a
  // tour is only ever as long as the width it is being taken at. Resizing
  // mid-tour is rare but it must not leave the index past the end.
  const steps = useMemo(
    () =>
      tour
        ? tour.steps.filter((s) => !s.width || (s.width === "wide") === wide)
        : [],
    [tour, wide],
  );
  const index = Math.min(running?.index ?? 0, Math.max(steps.length - 1, 0));

  // A step that belongs to a page takes you to that page. The overlay then waits
  // for the element to turn up on its own, which is also what covers the page
  // still being mid-fetch when it arrives.
  const stepPath = tour ? steps[index]?.path : undefined;
  useEffect(() => {
    if (stepPath && stepPath !== pathname) navigate(stepPath);
  }, [stepPath, pathname, navigate]);

  const startTour = useCallback(
    (id: TourId) => {
      // Choosing a tour answers the welcome as well. Otherwise somebody who
      // reached one of the "Show me around" buttons in the half-second before
      // the welcome resolved would find it waiting on the other side of the tour
      // they had just taken.
      if (state && !state.welcomed) save({ ...state, welcomed: true });
      setRunning({ id, index: 0 });
    },
    [state, save],
  );

  const closeTour = useCallback(
    (completed: boolean) => {
      const id = running?.id;
      setRunning(null);
      if (!id || !state) return;
      // Seen either way. What "seen" prevents is the app offering a tour nobody
      // asked for; somebody who left halfway has been offered it.
      if (!state.seen.includes(id)) save({ ...state, seen: [...state.seen, id] });
      if (completed) {
        toast.success(
          "That's the tour",
          "The help button in the corner has it again whenever you want it.",
        );
      }
    },
    [running, state, save, toast],
  );

  const api = useMemo<OnboardingApi>(
    () => ({
      startTour,
      hasSeen: (id) => !!state?.seen.includes(id),
      runningTour: running?.id ?? null,
    }),
    [startTour, state, running],
  );

  function answerWelcome(takeTour: boolean) {
    if (takeTour) startTour("basics");
    else if (state) save({ ...state, welcomed: true });
  }

  return (
    <OnboardingContext.Provider value={api}>
      {children}

      {state && (
        <WelcomeDialog
          visible={!state.welcomed && !running}
          name={person.data?.first_name || undefined}
          onTakeTour={() => answerWelcome(true)}
          onDismiss={() => answerWelcome(false)}
        />
      )}

      {tour && steps.length > 0 && (
        <TourOverlay
          tour={tour}
          steps={steps}
          index={index}
          onIndex={(i) => setRunning((r) => (r ? { ...r, index: i } : r))}
          onClose={closeTour}
        />
      )}
    </OnboardingContext.Provider>
  );
}

export function useOnboarding(): OnboardingApi {
  const ctx = useContext(OnboardingContext);
  if (!ctx)
    throw new Error("useOnboarding must be used within an OnboardingProvider");
  return ctx;
}
