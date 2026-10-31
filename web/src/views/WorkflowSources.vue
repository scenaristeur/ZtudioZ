<template>
  <div>
    <h1>Sources</h1>
    <div v-if="sources.length === 0" class="card empty">Aucune source trouvée</div>
    <div v-for="src in sources" :key="src.name" class="card source-card">
      <div class="source-header">
        <span class="source-icon">{{ src.type === 'directory' ? '\uD83D\uDCC1' : '\uD83D\uDCC4' }}</span>
        <strong>{{ src.name }}</strong>
        <span class="tag" v-if="src.type === 'directory'">{{ src.count }} fichiers</span>
        <span class="tag" v-else>{{ formatSize(src.size) }}</span>
      </div>
      <div v-if="src.type === 'directory' && expanded === src.name" class="source-contents">
        <div v-for="item in subItems" :key="item.name" class="source-item">
          <span>{{ item.type === 'directory' ? '\uD83D\uDCC1' : '\uD83D\uDCC4' }}</span>
          <span>{{ item.name }}</span>
          <span class="tag" v-if="item.size">{{ formatSize(item.size) }}</span>
        </div>
      </div>
      <button v-if="src.type === 'directory'" class="btn btn-sm" @click="toggleExpand(src.name)">
        {{ expanded === src.name ? 'Fermer' : 'Explorer' }}
      </button>
      <a v-if="src.url" :href="src.url" target="_blank" class="btn btn-sm">Ouvrir</a>
    </div>
  </div>
</template>

<script>
import { useProjectStore } from '../stores/project.js'
export default {
  name: 'WorkflowSources',
  setup() { return { store: useProjectStore() } },
  data() { return { sources: [], expanded: null, subItems: [] } },
  methods: {
    formatSize(b) {
      if (b > 1048576) return (b / 1048576).toFixed(1) + ' MB'
      if (b > 1024) return (b / 1024).toFixed(0) + ' KB'
      return b + ' B'
    },
    async toggleExpand(name) {
      if (this.expanded === name) { this.expanded = null; return }
      this.expanded = name
      const res = await fetch(`/api/assets/${this.store.slug}/sources/${name}`)
      const data = await res.json()
      this.subItems = data.items || []
    },
  },
  async mounted() {
    const res = await fetch(`/api/assets/${this.store.slug}/sources`)
    const data = await res.json()
    this.sources = data.sources || []
  },
}
</script>
