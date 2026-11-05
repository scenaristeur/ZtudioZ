<template>
  <div v-if="verdict" class="verdict" :class="verdict.ok ? 'ok' : 'ko'">
    <div class="verdict-head">
      <strong>{{ verdict.ok ? 'Verdict : OK' : 'Verdict : a corriger' }}</strong>
    </div>
    <p v-if="notes">{{ notes }}</p>
    <ul v-if="constraints.length">
      <li v-for="(constraint, index) in constraints" :key="index" :class="{ bad: !constraint.respected }">
        <span class="mark">{{ constraint.respected ? '✓' : '✗' }}</span>
        <span>{{ constraint.text }}</span>
        <small v-if="constraint.evidence">{{ constraint.evidence }}</small>
      </li>
    </ul>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'

const props = defineProps({
  url: { type: String, required: true },
})
const emit = defineEmits(['loaded'])

const verdict = ref(null)

const notes = computed(() => {
  const value = verdict.value?.notes
  return Array.isArray(value) ? value.join(' ') : value
})
const constraints = computed(() => verdict.value?.constraints || [])

onMounted(async () => {
  try {
    const response = await fetch(props.url)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const data = await response.json()
    verdict.value = data.verdict || data
    emit('loaded', verdict.value)
  } catch {
    verdict.value = null
  }
})
</script>
