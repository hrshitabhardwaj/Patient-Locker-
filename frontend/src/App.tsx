import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider, useAuth } from "./auth/AuthContext";
import Layout from "./components/Layout";
import { ApiError } from "./lib/api";
import ChangePasswordPage from "./pages/ChangePasswordPage";
import DashboardPage from "./pages/DashboardPage";
import LoginPage from "./pages/LoginPage";
import PatientPage from "./pages/PatientPage";
import PatientsPage from "./pages/PatientsPage";
import QueryLetterPage from "./pages/QueryLetterPage";
import ReviewQueuePage from "./pages/ReviewQueuePage";
import AuditLogPage from "./pages/AuditLogPage";
import UsersPage from "./pages/UsersPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Do not retry answers that will not change (signed out, no access, not found).
      retry: (count, error) =>
        !(error instanceof ApiError && [401, 403, 404, 422].includes(error.status)) && count < 2,
      refetchOnWindowFocus: true,
    },
  },
});

function Root() {
  const { state } = useAuth();
  if (state.status === "loading") {
    return <div className="p-8 text-sm text-slate-500">Loading…</div>;
  }
  if (state.status === "signed_out") return <LoginPage />;
  if (state.user.must_change_password) return <ChangePasswordPage />;
  return (
    <Routes>
      <Route element={<Layout user={state.user} />}>
        <Route index element={<DashboardPage />} />
        <Route path="patients" element={<PatientsPage />} />
        <Route path="patients/:patientId" element={<PatientPage />} />
        <Route path="letters/:letterId" element={<QueryLetterPage />} />
        <Route path="review" element={<ReviewQueuePage />} />
        {state.user.permissions.includes("users:manage") && <Route path="staff" element={<UsersPage />} />}
        {state.user.permissions.includes("audit:read") && <Route path="audit" element={<AuditLogPage />} />}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <Root />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
