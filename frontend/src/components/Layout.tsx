import { useQuery } from "@tanstack/react-query";
import { NavLink, Outlet } from "react-router-dom";

import { useAuth, type CurrentUser, type Role } from "../auth/AuthContext";
import { api } from "../lib/api";
import type { DashboardStats } from "../lib/types";

const ROLE_LABEL: Record<Role, string> = {
  admin: "Administrator",
  case_manager: "Case manager",
  staff: "Staff",
};

function NavItem({ to, children, end }: { to: string; children: React.ReactNode; end?: boolean }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        `flex items-center justify-between rounded-md px-3 py-2 text-sm font-medium ${
          isActive ? "bg-sky-50 text-sky-800" : "text-slate-600 hover:bg-slate-100"
        }`
      }
    >
      {children}
    </NavLink>
  );
}

export default function Layout({ user }: { user: CurrentUser }) {
  const { logout } = useAuth();
  const stats = useQuery({
    queryKey: ["dashboard-stats"],
    queryFn: () => api<DashboardStats>("/dashboard/stats"),
    refetchInterval: 15000,
  });
  const reviews = stats.data?.letters_awaiting_review ?? 0;

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 md:flex">
      <aside className="border-b border-slate-200 bg-white md:sticky md:top-0 md:flex md:h-screen md:w-60 md:shrink-0 md:flex-col md:border-r md:border-b-0">
        <div className="px-5 py-4">
          <p className="text-xs font-semibold tracking-wide text-sky-700 uppercase">Patient Locker</p>
          <p className="text-sm text-slate-500">Claims query desk</p>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-1 md:flex-col">
          <NavItem to="/" end>
            Dashboard
          </NavItem>
          <NavItem to="/patients">Patients</NavItem>
          {user.permissions.includes("users:manage") && <NavItem to="/staff">Staff accounts</NavItem>}
          {user.permissions.includes("audit:read") && <NavItem to="/audit">Audit log</NavItem>}
          <NavItem to="/review">
            Review queue
            {reviews > 0 && (
              <span className="ml-2 rounded-full bg-amber-100 px-2 text-xs font-semibold text-amber-900">
                {reviews}
              </span>
            )}
          </NavItem>
        </nav>
        <div className="hidden border-t border-slate-100 px-5 py-4 text-sm md:block">
          <p className="font-medium text-slate-800">{user.full_name}</p>
          <p className="mb-2 text-xs text-slate-500">{ROLE_LABEL[user.role]}</p>
          <button onClick={() => logout()} className="text-sm text-sky-700 hover:underline">
            Sign out
          </button>
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 md:px-8">
        <div className="mb-4 flex justify-end md:hidden">
          <button onClick={() => logout()} className="text-sm text-sky-700">
            Sign out ({user.full_name})
          </button>
        </div>
        <Outlet />
      </main>
    </div>
  );
}
