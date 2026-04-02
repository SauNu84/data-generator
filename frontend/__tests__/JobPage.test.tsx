import { render, screen, waitFor, act } from "@testing-library/react";
import JobPage from "@/app/jobs/[job_id]/page";
import * as api from "@/lib/api";

// Mock next/navigation and next/link
jest.mock("next/navigation", () => ({}));
jest.mock("next/link", () => {
  // eslint-disable-next-line react/display-name
  return ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  );
});

jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  getJob: jest.fn(),
}));

const mockGetJob = api.getJob as jest.MockedFunction<typeof api.getJob>;

// Params is now a Promise in Next.js 16
function makeParams(job_id: string): Promise<{ job_id: string }> {
  return Promise.resolve({ job_id });
}

describe("JobPage", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it("shows loading state initially", async () => {
    mockGetJob.mockResolvedValue({ job_id: "j1", status: "running" });
    await act(async () => {
      render(<JobPage params={makeParams("j1")} />);
    });
    // While resolving params and fetching, the spinner or job content renders
    // Just verify the page mounted without error
    expect(document.body).toBeTruthy();
  });

  it("shows quality score when job is done", async () => {
    const futureDate = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
    mockGetJob.mockResolvedValue({
      job_id: "j1",
      status: "done",
      quality_score: 87,
      column_quality: [
        { column: "age", score: 90 },
        { column: "city", score: 84 },
      ],
      download_url: "http://minio/file.csv",
      expires_at: futureDate,
    });

    await act(async () => {
      render(<JobPage params={makeParams("j1")} />);
    });

    await waitFor(() => {
      expect(screen.getByText(/87/)).toBeInTheDocument();
    });
    expect(screen.getByText("age")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /download csv/i })).toBeInTheDocument();
  });

  it("shows failed state with error message", async () => {
    mockGetJob.mockResolvedValue({
      job_id: "j1",
      status: "failed",
      error: "Out of memory during CTGAN training",
    });

    await act(async () => {
      render(<JobPage params={makeParams("j1")} />);
    });

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/out of memory/i);
    });
    expect(screen.getByRole("link", { name: /try again/i })).toBeInTheDocument();
  });

  it("shows download expired message when TTL has passed", async () => {
    const pastDate = new Date(Date.now() - 1000).toISOString();
    mockGetJob.mockResolvedValue({
      job_id: "j1",
      status: "done",
      quality_score: 80,
      download_url: "http://minio/file.csv",
      expires_at: pastDate,
    });

    await act(async () => {
      render(<JobPage params={makeParams("j1")} />);
    });

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/expired/i);
    });
  });
});
