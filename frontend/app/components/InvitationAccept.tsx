"use client";

import { FormEvent, useState } from "react";

export default function InvitationAccept({
  invitationToken,
  onComplete,
}: {
  invitationToken: string;
  onComplete: (email: string) => void;
}) {
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function accept(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setSubmitting(true);
    try {
      const response = await fetch("/api/v1/auth/invitations/accept", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: invitationToken, password }),
      });
      if (!response.ok) {
        const detail = (await response.json().catch(() => null))?.detail;
        setError(typeof detail === "string" ? detail : "This invitation is invalid, expired, or already used.");
        return;
      }
      const result = (await response.json()) as { email: string };
      onComplete(result.email);
    } catch {
      setError("Unable to reach the service. Check your connection and try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-panel">
        <div className="login-brand"><img src="/svc-logo.png" alt="SVC Infosystems" /><span>SVC ServiceFlow</span></div>
        <div className="login-copy"><p className="kicker">ACCOUNT INVITATION</p><h1>Welcome to ServiceFlow</h1><p>Set a password to activate your invited account.</p></div>
        <div className="login-footer"><span>Request to Resolution</span><span>v1.0</span></div>
      </section>
      <section className="login-form-wrap">
        <form className="login-form" onSubmit={(event) => void accept(event)}>
          <div className="mobile-brand"><img src="/svc-logo.png" alt="SVC Infosystems" /><strong>SVC ServiceFlow</strong></div>
          <p className="kicker">SET UP YOUR ACCOUNT</p>
          <h2>Choose a password</h2>
          <p className="form-help">Use at least 12 characters. This invitation can only be used once.</p>
          <label>Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={12} maxLength={72} autoComplete="new-password" required /></label>
          <label>Confirm password<input type="password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} minLength={12} maxLength={72} autoComplete="new-password" required /></label>
          {error && <p className="error" role="alert">{error}</p>}
          <button className="primary-button login-button" type="submit" disabled={submitting}>{submitting ? "Activating…" : "Activate account"}</button>
        </form>
      </section>
    </main>
  );
}
