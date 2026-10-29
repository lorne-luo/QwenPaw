import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import type { ColumnType } from "antd/es/table";
import { useSessionColumns } from "./columns";
import type { Session } from "./constants";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback ?? key,
  }),
}));

const handlers = {
  onEdit: vi.fn(),
  onDelete: vi.fn(),
  onView: vi.fn(),
  onArchiveToggle: vi.fn(),
};

function columnsFor(isArchivedTab: boolean) {
  const { result } = renderHook(() =>
    useSessionColumns({ ...handlers, isArchivedTab }),
  );
  return result.current as ColumnType<Session>[];
}

function column(cols: ColumnType<Session>[], key: string) {
  const col = cols.find((c) => c.key === key);
  if (!col) throw new Error(`missing column ${key}`);
  return col;
}

const session = (created_at: string | null, updated_at: string | null) =>
  ({ id: "s", created_at, updated_at }) as Session;

describe("useSessionColumns default sort", () => {
  it.each([
    ["active", false],
    ["archived", true],
  ])("%s tab sorts by UpdatedAt descending only", (_tab, isArchived) => {
    const cols = columnsFor(isArchived);
    expect(column(cols, "updated_at").defaultSortOrder).toBe("descend");
    expect(cols.filter((c) => c.defaultSortOrder).map((c) => c.key)).toEqual([
      "updated_at",
    ]);
  });

  it.each([
    ["active", false],
    ["archived", true],
  ])(
    "%s tab shows CreatedAt without a creation-time sorter",
    (_tab, isArchived) => {
      const created = column(columnsFor(isArchived), "created_at");
      expect(created.dataIndex).toBe("created_at");
      expect(created.sorter).toBeUndefined();
    },
  );

  it("UpdatedAt sorter ranks recent activity over a newer creation date", () => {
    const sorter = column(columnsFor(false), "updated_at").sorter as (
      a: Session,
      b: Session,
    ) => number;
    const busy = session("2026-09-01T12:00:00Z", "2026-09-20T12:00:00Z");
    const idle = session("2026-09-10T12:00:00Z", "2026-09-10T13:00:00Z");
    expect(sorter(busy, idle)).toBeGreaterThan(0);
  });

  it("UpdatedAt sorter falls back to created_at and puts untimed rows lowest", () => {
    const sorter = column(columnsFor(false), "updated_at").sorter as (
      a: Session,
      b: Session,
    ) => number;
    const createdOnly = session("2026-09-15T12:00:00Z", null);
    const idle = session("2026-09-10T12:00:00Z", "2026-09-10T13:00:00Z");
    const untimed = session(null, null);
    expect(sorter(createdOnly, idle)).toBeGreaterThan(0);
    expect(sorter(untimed, idle)).toBeLessThan(0);
  });
});
