<template>
  <div>
    <h1>Montage</h1>
    <div class="card toolbar">
      <label>Scène :</label>
      <select v-model="sceneNum" @change="load" class="select">
        <option v-for="s in scenes" :key="s.number" :value="s.number">Scène {{ s.number }}</option>
      </select>
      <button class="btn primary" @click="assemble" :disabled="loading" style="margin-left: 1rem;">
        {{ loading ? 'Assembly...' : 'Assembler la scène' }}
      </button>
    </div>

    <div v-if="montage" class="card">
      <video :src="montage.url" controls class="montage-video" />
      <div class="montage-info">
        <span class="tag">{{ formatSize(montage.size) }}</span>
      </div>
    </div>
    <div v-else class="card empty">Aucun montage pour cette scène</div>

    <div class="card" style="margin-top: 1rem;">
      <h2>Toutes les scènes</h2>
      <div v-for="s in scenes" :key="s.number" class="montage-row">
        <span>Scène {{ s.number }}</span>
        <button class="btn btn-sm" @click="sceneNum = s.number; load()">Voir</button>
      </div>
    </div>
  </div>
</template>

<script>
import { useProjectStore } from '../stores/project.js'
export default {
  name: 'WorkflowMontage',
  setup() { return { store: useProjectStore() } },
  data() { return { sceneNum: 1, montage: null, loading: false } },
  computed: { scenes() { return this.store.scenes } },
  methods: {
    formatSize(b) { return b > 1048576 ? (b/1048576).toFixed(1)+' MB' : (b/1024).toFixed(0)+' KB' },
    async load() {
      this.montage = null
      try {
        const res = await fetch(`/api/assets/${this.store.slug}/montage/${this.sceneNum}`)
        if (res.ok) this.montage = await res.json()
      } catch (e) { /* */ }
    },
    async assemble() {
      this.loading = true
      await this.store.runStep('montage', this.sceneNum)
      this.loading = false
      setTimeout(() => this.load(), 2000)
    },
  },
  mounted() { this.load() },
}
</script>
