/**
 * Multi-Provider Routing Example
 * 
 * Demonstrates intelligent provider fallback using circuit breakers and health scoring.
 * Automatically routes to the healthiest provider (OpenRouter → Groq → Gemini → HuggingFace).
 */

import { InfrGateControlPlane } from '@infrgate/control-plane';

// Initialize control plane with circuit breakers
const cp = new InfrGateControlPlane({
  circuitBreaker: {
    failureThreshold: 5,      // Open circuit after 5 failures
    recoveryTimeoutMs: 30000, // Wait 30s before retry
    successThreshold: 2,       // Need 2 successes to fully close
  },
});

/**
 * Call a provider with automatic health tracking
 */
async function callProvider(
  provider: string,
  prompt: string
): Promise<{ content: string; model: string }> {
  const endpoints = {
    openai: 'https://api.openai.com/v1/chat/completions',
    groq: 'https://api.groq.com/openai/v1/chat/completions',
    gemini: 'https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent',
    huggingface: 'https://api-inference.huggingface.co/models/meta-llama/Meta-Llama-3-8B-Instruct',
  };

  const startTime = Date.now();
  const breaker = cp.getCircuitBreaker(provider);

  try {
    const result = await breaker.execute(async () => {
      const response = await fetch(endpoints[provider as keyof typeof endpoints], {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: [{ role: 'user', content: prompt }],
          max_tokens: 256,
        }),
      });

      if (!response.ok) {
        throw new Error(`${provider} returned ${response.status}`);
      }

      return response.json();
    });

    // Record success with latency
    const latency = Date.now() - startTime;
    cp.scorer.record(provider, latency, false);

    console.log(`✅ ${provider} succeeded (${latency}ms)`);
    return result;
  } catch (error) {
    // Record failure with latency
    const latency = Date.now() - startTime;
    cp.scorer.record(provider, latency, true);

    console.log(`❌ ${provider} failed (${latency}ms):`, (error as Error).message);
    throw error;
  }
}

/**
 * Execute request with automatic provider ranking and fallback
 */
async function generateCompletion(prompt: string) {
  const providers = ['openai', 'groq', 'gemini', 'huggingface'];

  // Rank providers by health score (best first)
  const ranked = cp.rankProviders(providers);
  console.log(`\n🎯 Provider ranking: ${ranked.join(' → ')}\n`);

  // Print health scores
  for (const provider of ranked) {
    const health = cp.scorer.getHealth(provider);
    const score = cp.scorer.calculateScore(provider);
    console.log(
      `   ${provider}: score=${score.toFixed(2)}, ` +
      `availability=${health.availability.toFixed(2)}, ` +
      `errorRate=${health.ewmaErrorRate.toFixed(2)}, ` +
      `latency=${health.ewmaLatencyMs.toFixed(0)}ms`
    );
  }
  console.log();

  // Try providers in ranked order
  for (const provider of ranked) {
    try {
      const result = await callProvider(provider, prompt);
      console.log(`\n✨ Final result from ${provider}:`, result.content.slice(0, 100), '...\n');
      return result;
    } catch (error) {
      console.log(`   Trying next provider...\n`);
    }
  }

  throw new Error('All providers unavailable');
}

// Example usage
async function main() {
  console.log('=== Multi-Provider LLM Gateway Example ===\n');

  try {
    await generateCompletion('Explain quantum computing in simple terms');
  } catch (error) {
    console.error('Fatal error:', (error as Error).message);
  }
}

// Run if executed directly
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch(console.error);
}

export { generateCompletion };
