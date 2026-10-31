<template>
  <div>
    <h1>Scénario</h1>
    <div class="card">
      <button class="btn primary" @click="generate" :disabled="loading">
        {{ loading ? 'Génération...' : 'Générer le scénario' }}
      </button>
    </div>
    <div v-if="scenario" class="card">
      <div v-for="(act, i) in scenario.acts || []" :key="i" class="card">
        <h2>Acte {{ i + 1 }}</h2>
        <p>{{ typeof act === 'string' ? act : act.summary || JSON.stringify(act) }}</p>
      </div>
    </div>
    <div v-else class="card empty">Aucun scénario généré</div>
  </div>
</template>

<script>
import { useProjectStore } from '../stores/project.js'
export default {
  name: 'WorkflowScenario',
  setup() { return { store: useProjectStore() } },
  data() { return { scenario: null, loading: false } },
  methods: {
    async generate() {
      this.loading = true
      await this.store.runStep('scenario')
      this.loading = false
    },
  },
  async mounted() {
    try {
      const res = await fetch(`/api/history/${this.store.slug}/versions?kind=scenario`)
      const data = await res.json()
      if (data.versions?.length) {
        const vRes = await fetch(`/api/history/${this.store.slug}/version/${data.versions[0].hash}`)
        this.scenario = await vRes.json()
      }
    } catch (e) { /* ignore */ }
  },
}
</script>
