/**
 * Auth utilities — token storage, user session management.
 * Uses localStorage for access + refresh tokens (SPA pattern).
 */

const ACCESS_KEY = "sdg_access_token";
const REFRESH_KEY = "sdg_refresh_token";
const USER_KEY = "sdg_user";

export interface UserProfile {
  id: string;
  email: string;
  tier: "free" | "pro" | "enterprise";
  is_email_verified: boolean;
  created_at: string;
}

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(REFRESH_KEY);
}

export function getUser(): UserProfile | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as UserProfile;
  } catch {
    return null;
  }
}

export function saveSession(accessToken: string, refreshToken: string, user: UserProfile): void {
  localStorage.setItem(ACCESS_KEY, accessToken);
  localStorage.setItem(REFRESH_KEY, refreshToken);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession(): void {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USER_KEY);
}

export function isAuthenticated(): boolean {
  return !!getAccessToken();
}
