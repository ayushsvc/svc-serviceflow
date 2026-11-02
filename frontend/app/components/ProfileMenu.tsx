"use client";

import { useState } from "react";
import { Popover } from "./Popover";

type Props = {
  user: { id: number; name: string; email: string; entity?: { id: number; name: string } | null; profile: { name: string } } | null;
  authorization: string;
  onSignOut: () => Promise<void> | void;
};

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "?";
}

export default function ProfileMenu({ user, onSignOut }: Props) {
  const entityName = user?.entity?.name ?? "";
  const [signingOut, setSigningOut] = useState(false);
  return (
    <Popover
      wrapClassName="profile-menu-wrap"
      triggerClassName="avatar avatar-indigo top-avatar"
      panelClassName="profile-menu"
      ariaLabel="Account menu"
      label={initials(user?.name ?? "")}
    >
      {() => <>
        <div className="profile-menu-head">
          <span className="avatar avatar-indigo" aria-hidden="true">{initials(user?.name ?? "")}</span>
          <div>
            <strong>{user?.name ?? "Signed-in user"}</strong>
            <small>{user?.email}</small>
          </div>
        </div>
        <dl className="profile-menu-details">
          <div><dt>Profile</dt><dd>{user?.profile.name ?? "—"}</dd></div>
          <div><dt>Entity</dt><dd>{entityName || "—"}</dd></div>
        </dl>
        <button type="button" role="menuitem" className="profile-menu-signout" disabled={signingOut} onClick={() => { setSigningOut(true); void Promise.resolve(onSignOut()); }}>
          {signingOut ? "Signing out…" : "Sign out"}
        </button>
      </>}
    </Popover>
  );
}
