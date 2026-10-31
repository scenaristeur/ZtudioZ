<template>
  <div class="app">
    <nav class="sidebar">
      <div class="logo">ZtudioZ</div>
      <div class="project-name">{{ slug }}</div>
      <ul class="nav-steps">
        <li v-for="step in steps" :key="step.id"
            :class="{ active: currentStep === step.id, done: stepDone(step.id) }"
            @click="currentStep = step.id">
          <span class="step-icon">{{ stepDone(step.id) ? '\u2713' : step.icon }}</span>
          <span class="step-label">{{ step.label }}</span>
        </li>
      </ul>
      <div class="status-bar">
        <div class="dot" :class="connected ? 'green' : 'red'" />
        {{ connected ? 'Connected' : 'Offline' }}
      </div>
    </nav>
    <main class="content">
      <WorkflowInit v-if="currentStep === 'init'" />
      <WorkflowSources v-if="currentStep === 'sources'" />
      <WorkflowScenario v-if="currentStep === 'scenario'" />
      <WorkflowStoryboard v-if="currentStep === 'storyboard'" />
      <WorkflowShots v-if="currentStep === 'shots'" />
      <WorkflowVideo v-if="currentStep === 'video'" />
      <WorkflowAudio v-if="currentStep === 'audio'" />
      <WorkflowMontage v-if="currentStep === 'montage'" />
    </main>
  </div>
</template>

<script>
import { useProjectStore } from './stores/project.js'
import WorkflowInit from './views/WorkflowInit.vue'
import WorkflowSources from './views/WorkflowSources.vue'
import WorkflowScenario from './views/WorkflowScenario.vue'
import WorkflowStoryboard from './views/WorkflowStoryboard.vue'
import WorkflowShots from './views/WorkflowShots.vue'
import WorkflowVideo from './views/WorkflowVideo.vue'
import WorkflowAudio from './views/WorkflowAudio.vue'
import WorkflowMontage from './views/WorkflowMontage.vue'

export default {
  name: 'App',
  components: {
    WorkflowInit, WorkflowSources, WorkflowScenario,
    WorkflowStoryboard, WorkflowShots, WorkflowVideo,
    WorkflowAudio, WorkflowMontage,
  },
  setup() {
    const store = useProjectStore()
    return { store }
  },
  data() {
    return {
      currentStep: 'init',
      steps: [
        { id: 'init', icon: '⚙', label: 'Initialisation' },
        { id: 'sources', icon: '📄', label: 'Sources' },
        { id: 'scenario', icon: '📝', label: 'Scénario' },
        { id: 'storyboard', icon: '🎬', label: 'Storyboard' },
        { id: 'shots', icon: '🖼', label: 'Shots / Images' },
        { id: 'video', icon: '🎬', label: 'Vidéos' },
        { id: 'audio', icon: '🎧', label: 'Audio' },
        { id: 'montage', icon: '🎮', label: 'Montage' },
      ],
    }
  },
  computed: {
    slug() { return this.store.slug },
    connected() { return !!this.store.ws },
    scenes() { return this.store.scenes },
  },
  methods: {
    stepDone(id) {
      if (!this.store.status) return false
      const s = this.store.status
      if (id === 'init') return s.scenes?.length > 0
      if (id === 'sources') return s.scenes?.length > 0
      if (id === 'scenario') return s.history?.total_versions > 0
      if (id === 'storyboard') return s.scenes?.some(sc => sc.has_storyboard)
      if (id === 'shots') return s.video_count > 0
      if (id === 'video') return s.video_count > 0
      if (id === 'audio') return s.audio_count > 0
      if (id === 'montage') return false
      return false
    },
  },
  mounted() {
    this.store.init()
  },
  beforeUnmount() {
    this.store.disconnectWS()
  },
}
</script>
