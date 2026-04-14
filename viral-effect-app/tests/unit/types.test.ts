/**
 * Smoke tests for type module — verifies ERROR_CODES are defined correctly.
 */

import { describe, it, expect } from 'vitest';
import { ERROR_CODES } from '$lib/types';

describe('ERROR_CODES', () => {
  it('has all 9 error codes', () => {
    expect(Object.keys(ERROR_CODES)).toHaveLength(9);
  });

  it('E4 is MODEL_DOWNLOAD_FAILED', () => {
    expect(ERROR_CODES.E4).toBe('MODEL_DOWNLOAD_FAILED');
  });

  it('E9 is WORKER_CRASHED', () => {
    expect(ERROR_CODES.E9).toBe('WORKER_CRASHED');
  });
});
