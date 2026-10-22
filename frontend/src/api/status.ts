import type { PipelineStatus } from '../core/types';

/**
 * Fetch the pipeline status (which carries the backend version) from
 * GET /api/status. Returns undefined on an HTTP error (a JSON body can
 * never parse to undefined); otherwise the parsed body, unchecked.
 * Network and JSON errors throw.
 */
export async function fetchPipelineStatus(): Promise<PipelineStatus | undefined> {
  const response = await fetch('/api/status');
  if (!response.ok) {
    return undefined;
  }
  return response.json();
}
