import { createApp } from 'https://cdn.jsdelivr.net/npm/vue@3.4.27/dist/vue.esm-browser.prod.js';
import { loadPartials } from './partials.js';
import { createInitialState } from './workload/state.js';
import { computedDefinitions } from './workload/computed.js';
import { methodDefinitions } from './workload/methods.js';
import { mountedHook, beforeUnmountHook } from './workload/lifecycle.js';

await loadPartials();

createApp({
  data: createInitialState,
  computed: computedDefinitions,
  methods: methodDefinitions,
  mounted: mountedHook,
  beforeUnmount: beforeUnmountHook,
}).mount('#workload-app');
