"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { saveSession, UserProfile } from "@/lib/auth";

/**
 * Handles the Google OAuth redirect.
 * Backend redirects to: /auth/callback#access_token=...&refresh_token=...
 */
export default function AuthCallbackPage() {
  const router = useRouter();

  useEffect(() => {
    const hash = window.location.hash.slice(1);
    const params = new URLSearchParams(hash);
    const accessToken = params.get("access_token");
    const refreshToken = params.get("refresh_token");

    if (!accessToken || !refreshToken) {
      router.replace("/login?error=oauth_failed");
      return;
    }

    // Fetch user profile with the new token
    fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/auth/me`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
      .then((r) => r.json())
      .then((user: UserProfile) => {
        saveSession(accessToken, refreshToken, user);
        router.replace("/dashboard");
      })
      .catch(() => {
        router.replace("/login?error=oauth_failed");
      });
  }, [router]);

  return (
    <main className="min-h-screen flex items-center justify-center bg-gray-50">
      <p className="text-sm text-gray-500">Signing you in…</p>
    </main>
  );
}
