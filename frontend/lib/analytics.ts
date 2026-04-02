// Analytics stub — real provider (Segment / Mixpanel) to be wired in Week 4.
// Fires events via window.analytics.track if present, otherwise logs to console.

type AnalyticsEvent =
  | "upload_success"
  | "generate_initiated"
  | "results_viewed"
  | "download_clicked";

interface EventProperties {
  [key: string]: string | number | boolean | undefined;
}

declare global {
  interface Window {
    analytics?: { track: (event: string, props?: EventProperties) => void };
  }
}

export function track(event: AnalyticsEvent, props?: EventProperties): void {
  if (typeof window === "undefined") return;
  if (window.analytics?.track) {
    window.analytics.track(event, props);
  } else {
    console.debug("[analytics]", event, props);
  }
}
