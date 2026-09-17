const DAY_MS = 86_400_000;

function utcDay(value: string): number {
  const timestamp = Date.parse(`${value}T00:00:00Z`);
  if (!Number.isFinite(timestamp)) throw new Error(`Invalid calendar date: ${value}`);
  return timestamp;
}

function isoDay(timestamp: number): string {
  return new Date(timestamp).toISOString().slice(0, 10);
}

/** Return the equal-length calendar period immediately before the activity. */
export function priorEqualPeriod(startDate: string, endDate: string) {
  const start = utcDay(startDate);
  const end = utcDay(endDate);
  if (end < start) throw new Error("Activity end date is before its start date");
  const days = Math.round((end - start) / DAY_MS) + 1;
  const baselineEnd = start - DAY_MS;
  const baselineStart = baselineEnd - (days - 1) * DAY_MS;
  return { start: isoDay(baselineStart), end: isoDay(baselineEnd) };
}
