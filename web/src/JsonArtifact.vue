<template>
  <details class="json-artifact" @toggle="load">
    <summary>{{ label }}</summary>
    <pre v-if="data">{{ data }}</pre>
    <p v-else-if="error" class="error">{{ error }}</p>
    <p v-else class="muted small">chargement…</p>
  </details>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({
  url: { type: String, required: true },
  label: { type: String, default: 'voir le JSON' },
})

const data = ref('')
const error = ref('')

async function load() {
  if (data.value || error.value) return
  try {
    const response = await fetch(props.url)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    data.value = JSON.stringify(await response.json(), null, 2)
  } catch (exc) {
    error.value = exc.message
  }
}
</script>
