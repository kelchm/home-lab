import { describe, expect, test } from 'vitest';
import { pageRequiredRefs } from '../src/domains/contract';
import { domain } from '../src/domains/sparks';
import { range } from './fixtures';

describe('inference evidence follows the selected source and window', () => {
  for (const id of ['inference', 'deployment']) {
    const page = domain.pages.find((item) => item.id === id)!;
    test(`${id} uses the historical identity only for its dated recording`, () => {
      const historical = range(1793487600000, 1793491500000, [1793487600000, 1793491500000]);
      expect(pageRequiredRefs(page, { timeRange: historical, mode: 'recorded', entity: null })).toEqual(['D_spark_identity']);
      expect(pageRequiredRefs(page, { timeRange: range(), mode: 'recorded', entity: null })).toEqual(['D_spark_serving']);
      expect(pageRequiredRefs(page, { timeRange: historical, mode: 'live', entity: null })).toEqual(['D_spark_serving']);
    });
  }
});
