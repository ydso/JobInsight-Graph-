<template>
  <div ref="chartEl" class="chart-canvas" role="img" :aria-label="ariaLabel"></div>
</template>

<script setup>
import { BarChart, GraphChart, HeatmapChart, LineChart, PieChart, RadarChart } from "echarts/charts";
import {
  AriaComponent,
  GridComponent,
  LegendComponent,
  RadarComponent,
  TooltipComponent,
  VisualMapComponent
} from "echarts/components";
import { init, use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";

use([
  AriaComponent,
  BarChart,
  CanvasRenderer,
  GraphChart,
  GridComponent,
  HeatmapChart,
  LegendComponent,
  LineChart,
  PieChart,
  RadarChart,
  RadarComponent,
  TooltipComponent,
  VisualMapComponent
]);

const props = defineProps({
  option: {
    type: Object,
    required: true
  },
  ariaLabel: {
    type: String,
    default: "数据图表"
  }
});

const emit = defineEmits(["chart-click"]);
const chartEl = ref(null);
let chart = null;
let resizeObserver = null;

function applyOption() {
  if (!chart || !props.option) return;
  chart.setOption(props.option, true);
}

onMounted(() => {
  chart = init(chartEl.value, null, { renderer: "canvas" });
  chart.on("click", (event) => emit("chart-click", event));
  applyOption();

  resizeObserver = new ResizeObserver(() => chart?.resize());
  resizeObserver.observe(chartEl.value);
});

watch(
  () => props.option,
  () => applyOption(),
  { deep: true }
);

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  chart?.dispose();
  chart = null;
});
</script>
