import { useQuery } from "@tanstack/react-query";

import { Card, Empty, ErrorBox, Spinner } from "../components/ui";
import { api } from "../lib/api";
import type { LetterRow } from "../lib/types";
import { LetterTable } from "./DashboardPage";

export default function ReviewQueuePage() {
  const letters = useQuery({
    queryKey: ["review-queue"],
    queryFn: () => api<LetterRow[]>("/dashboard/letters?status=awaiting_review&limit=100"),
    refetchInterval: 5000,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Review queue</h1>
        <p className="text-sm text-slate-500">
          Letters held because something is uncertain. Nothing is sent to these patients until a
          staff member resolves each point; sending then happens automatically.
        </p>
      </div>
      <ErrorBox error={letters.error} />
      <Card>
        {letters.isLoading ? (
          <Spinner label="Loading…" />
        ) : !letters.data?.length ? (
          <Empty>Nothing is waiting for review.</Empty>
        ) : (
          <LetterTable rows={letters.data} />
        )}
      </Card>
    </div>
  );
}
