<template>
  <div class="audio-player">
    <div class="player-header">
      <p class="studio-label">Monitor</p>
      <h3>试听</h3>
    </div>

    <div v-if="audioPath" class="player-content">
      <audio
        ref="audioRef"
        :src="audioSrc"
        @play="onPlay"
        @pause="onPause"
        @loadedmetadata="onLoadedMetadata"
        @timeupdate="onTimeUpdate"
        @ended="onEnded"
        @error="onAudioError"
      />

      <div class="transport">
        <el-button
          class="play-btn"
          type="primary"
          circle
          size="large"
          @click="togglePlay"
        >
          <el-icon :size="20">
            <VideoPause v-if="isPlaying" />
            <VideoPlay v-else />
          </el-icon>
        </el-button>
        <div class="progress-bar">
          <div class="time-row">
            <span class="studio-mono">{{ formatTime(currentTime) }}</span>
            <span class="studio-mono dim">{{ formatTime(duration) }}</span>
          </div>
          <el-slider
            v-model="progress"
            :max="Math.max(duration, 0.1)"
            :show-tooltip="false"
            @input="seek"
          />
        </div>
        <el-button circle title="导出 MP3" @click="$emit('download')">
          <el-icon><Download /></el-icon>
        </el-button>
      </div>
    </div>

    <div v-else class="empty-monitor">
      <div class="waveform-bar muted" aria-hidden="true"><span v-for="n in 12" :key="n" /></div>
      <p>合成完成后可在这里试听与导出</p>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { VideoPlay, VideoPause, Download } from '@element-plus/icons-vue'
import { toAudioUrl } from '../stores/editor'

const props = defineProps({
  audioPath: String,
})

const emit = defineEmits(['download', 'error'])

const audioRef = ref(null)
const isPlaying = ref(false)
const currentTime = ref(0)
const duration = ref(0)

const audioSrc = computed(() => (props.audioPath ? toAudioUrl(props.audioPath) : ''))
const progress = computed({
  get: () => currentTime.value,
  set: (val) => {
    currentTime.value = val
  },
})

watch(
  () => props.audioPath,
  () => {
    isPlaying.value = false
    currentTime.value = 0
    duration.value = 0
    if (audioRef.value) audioRef.value.load()
  }
)

async function play() {
  if (!audioRef.value || !props.audioPath) return
  isPlaying.value = true
  try {
    await audioRef.value.play()
  } catch (error) {
    isPlaying.value = false
    emit('error', error)
  }
}

function togglePlay() {
  if (!audioRef.value) return
  if (isPlaying.value) {
    audioRef.value.pause()
    return
  }
  play()
}

function onPlay() {
  isPlaying.value = true
}
function onPause() {
  isPlaying.value = false
}
function onLoadedMetadata() {
  if (!audioRef.value) return
  duration.value = audioRef.value.duration || 0
}
function onTimeUpdate() {
  if (!audioRef.value) return
  currentTime.value = audioRef.value.currentTime
  duration.value = audioRef.value.duration || duration.value
}
function onEnded() {
  isPlaying.value = false
  currentTime.value = 0
}
function onAudioError() {
  isPlaying.value = false
  emit('error', new Error('音频加载失败'))
}
function seek(val) {
  if (audioRef.value) audioRef.value.currentTime = val
}
function formatTime(seconds) {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
}

defineExpose({ play })
</script>

<style scoped>
.audio-player {
  background: var(--studio-panel);
  border: 1px solid var(--studio-line);
  border-radius: var(--studio-radius);
  padding: 18px;
}

.player-header {
  margin-bottom: 14px;
}

.player-header h3 {
  margin: 4px 0 0;
  font-size: 16px;
}

.transport {
  display: flex;
  align-items: center;
  gap: 14px;
}

.progress-bar {
  flex: 1;
  min-width: 0;
}

.time-row {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--studio-muted);
  margin-bottom: 2px;
}

.time-row .dim {
  opacity: 0.7;
}

.empty-monitor {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 22px 12px;
  border-radius: var(--studio-radius-sm);
  background: #0a0d13;
  border: 1px dashed var(--studio-line);
  color: var(--studio-muted);
  font-size: 13px;
  text-align: center;
}

.empty-monitor .waveform-bar {
  width: 88px;
  height: 20px;
  opacity: 0.35;
}

.empty-monitor .waveform-bar span {
  background: var(--studio-muted);
  animation: none;
  transform: scaleY(0.6);
}
</style>
