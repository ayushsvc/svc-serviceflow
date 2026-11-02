"use client";

import { FormEvent, useEffect, useState } from "react";

type UserRow = {
  id: number;
  name: string;
  email: string;
  is_active: boolean;
  profile: { name: string; interface: string };
};
type Options = {
  profiles: Array<{ id: number; name: string; interface: string }>;
  teams: Array<{ id: number; name: string }>;
};

export default function UserManagementPanel({
  authorization,
  currentUserId,
}: {
  authorization: string;
  currentUserId: number;
}) {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [options, setOptions] = useState<Options>({ profiles: [], teams: [] });
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [profileId, setProfileId] = useState("");
  const [teamIds, setTeamIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const selectedProfile = options.profiles.find((profile) => String(profile.id) === profileId);

  async function load() {
    setError("");
    try {
      const headers = { Authorization: authorization };
      const [usersResponse, optionsResponse] = await Promise.all([
        fetch("/api/v1/auth/users", { headers }),
        fetch("/api/v1/auth/invitations/options", { headers }),
      ]);
      if (!usersResponse.ok || !optionsResponse.ok) throw new Error("Unable to load users or invitation options.");
      const [userRows, invitationOptions] = await Promise.all([usersResponse.json(), optionsResponse.json()]);
      setUsers(userRows as UserRow[]);
      setOptions(invitationOptions as Options);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load users.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [authorization]);

  async function submitInvite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/v1/auth/invitations", {
        method: "POST",
        headers: { Authorization: authorization, "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          email,
          profile_id: Number(profileId),
          team_ids: selectedProfile?.interface === "helpdesk" ? teamIds.map(Number) : [],
        }),
      });
      if (!response.ok) {
        const detail = (await response.json().catch(() => null))?.detail;
        throw new Error(typeof detail === "string" ? detail : "Invitation could not be created.");
      }
      setName("");
      setEmail("");
      setProfileId("");
      setTeamIds([]);
      setNotice("Invitation sent. The account stays inactive until the invitee sets a password.");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Invitation could not be sent.");
    } finally {
      setBusy(false);
    }
  }

  async function resend(userId: number) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`/api/v1/auth/invitations/${userId}/resend`, {
        method: "POST",
        headers: { Authorization: authorization },
      });
      if (!response.ok) {
        const detail = (await response.json().catch(() => null))?.detail;
        throw new Error(typeof detail === "string" ? detail : "Invitation could not be resent.");
      }
      setNotice("A fresh invitation link was sent. Any earlier link is now invalid.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Invitation could not be resent.");
    } finally {
      setBusy(false);
    }
  }

  async function setActive(user: UserRow, isActive: boolean) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`/api/v1/auth/users/${user.id}`, {
        method: "PATCH",
        headers: { Authorization: authorization, "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: isActive }),
      });
      if (!response.ok) {
        const detail = (await response.json().catch(() => null))?.detail;
        throw new Error(typeof detail === "string" ? detail : "Account status could not be changed.");
      }
      setNotice(isActive ? "Account activated." : "Account deactivated.");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Account status could not be changed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="user-management">
      <section className="table-card admin-form-card">
        <div className="panel-card-heading"><div><h2>Invite a user</h2><span>Accounts are assigned a profile here and cannot choose their own permissions.</span></div></div>
        <form className="form-grid" onSubmit={(event) => void submitInvite(event)}>
          <label>Name<input value={name} onChange={(event) => setName(event.target.value)} maxLength={160} autoComplete="name" required /></label>
          <label>Work email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} maxLength={320} autoComplete="email" required /></label>
          <label>Profile<select value={profileId} onChange={(event) => { setProfileId(event.target.value); setTeamIds([]); }} required><option value="">Choose a profile</option>{options.profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label>
          {selectedProfile?.interface === "helpdesk" && options.teams.length > 0 && <label>Teams<select multiple value={teamIds} onChange={(event) => setTeamIds(Array.from(event.target.selectedOptions, (option) => option.value))}>{options.teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}</select><small>Use Ctrl or Command to select multiple teams.</small></label>}
          <div className="user-invite-actions"><button className="primary-button" type="submit" disabled={busy || !profileId}>{busy ? "Working…" : "Send invitation"}</button></div>
        </form>
      </section>

      {notice && <p className="settings-saved" role="status">{notice}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <section className="table-card user-list-card">
        <div className="panel-card-heading"><h2>Users in this entity</h2><button className="secondary-button" type="button" onClick={() => void load()}>Refresh</button></div>
        {loading ? <p className="muted">Loading users…</p> : users.length === 0 ? <div className="empty-table"><strong>No users found</strong><span>Invited users will appear here.</span></div> : users.map((user) => (
          <div className="user-management-row" key={user.id}>
            <div><strong>{user.name}</strong><small>{user.email}</small></div>
            <span className="person-role">{user.profile.name}</span>
            <span className={`user-account-status ${user.is_active ? "is-active" : "is-pending"}`}>{user.is_active ? "Active" : "Invitation pending"}</span>
            {!user.is_active
              ? <button className="secondary-button" type="button" disabled={busy} onClick={() => void resend(user.id)}>Resend invite</button>
              : user.id !== currentUserId && <button className="secondary-button" type="button" disabled={busy} onClick={() => void setActive(user, false)}>Deactivate</button>}
          </div>
        ))}
      </section>
    </div>
  );
}
