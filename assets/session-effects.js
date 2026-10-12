/*
 * Token Meter page-header signal-effects adapter.
 * Rendering primitives: @paper-design/shaders 0.0.80, Apache-2.0.
 * See vendor/paper-shaders/LICENSE and NOTICE.
 */

import { ShaderMount } from "./vendor/paper-shaders/shader-mount.js";
import { ShaderFitOptions } from "./vendor/paper-shaders/shader-sizing.js";
import { getShaderColorFromString } from "./vendor/paper-shaders/get-shader-color-from-string.js";
import { getShaderNoiseTexture } from "./vendor/paper-shaders/get-shader-noise-texture.js";
import {
  PulsingBorderAspectRatios,
  pulsingBorderFragmentShader,
} from "./vendor/paper-shaders/shaders/pulsing-border.js";

const WEBGL_OPTIONS = {
  alpha: true,
  antialias: true,
  premultipliedAlpha: true,
  preserveDrawingBuffer: false,
  powerPreference: "low-power",
};

const noiseTexture = getShaderNoiseTexture();
const orderedDitherRainFragmentShader = `#version 300 es
precision mediump float;

uniform float u_time;
uniform vec2 u_resolution;
uniform float u_pixelRatio;
uniform vec4 u_colorBack;
uniform vec4 u_colorFront;
uniform float u_strength;
uniform float u_pxSize;

out vec4 fragColor;

float hash11(float value) {
  return fract(sin(value * 127.1) * 43758.5453);
}

float hash21(vec2 value) {
  return fract(sin(dot(value, vec2(127.1, 311.7))) * 43758.5453);
}

const int bayer4x4[16] = int[16](
  0, 8, 2, 10,
  12, 4, 14, 6,
  3, 11, 1, 9,
  15, 7, 13, 5
);

float bayerThreshold(vec2 cell) {
  ivec2 position = ivec2(mod(cell, 4.));
  return float(bayer4x4[position.y * 4 + position.x]) / 16.;
}

float fallingPhase(vec2 cell, float speed, float offset, float period) {
  return mod(cell.y + u_time * speed + offset, period);
}

void main() {
  float pixelSize = max(1.5, u_pxSize * u_pixelRatio);
  vec2 cell = floor(gl_FragCoord.xy / pixelSize);
  float column = cell.x;
  float columnSeed = hash11(column * 31.17);
  float speed = mix(8., 18., columnSeed);
  float period = mix(20., 38., hash11(column * 17.91 + 3.));
  float offset = hash11(column * 71.7) * period;
  float phase = fallingPhase(cell, speed, offset, period);
  float streamLength = mix(7., 16., hash11(column * 11.3 + 9.));
  float trail = 1. - smoothstep(0., streamLength, phase);
  float streamHead = 1. - smoothstep(0., 1.25, phase);
  float columnOn = step(.2, hash11(column * 43.1));
  float breakNoise = hash21(cell + vec2(column * 2.7, 0.));
  float orderedPixel = step(bayerThreshold(cell), trail * (.9 + .25 * breakNoise));
  float ink = max(streamHead, orderedPixel * step(.12, breakNoise)) * columnOn;
  float haze = trail * columnOn * (1. - ink) * .35;
  vec3 color = mix(u_colorBack.rgb, u_colorFront.rgb, ink);
  float alpha = (ink * u_colorFront.a + haze * u_colorBack.a) * u_strength;
  fragColor = vec4(color * alpha, alpha);
}
`;

function readyNoiseTexture() {
  if (!noiseTexture) return Promise.reject(new Error("Noise texture unavailable"));
  if (noiseTexture.complete && noiseTexture.naturalWidth) return Promise.resolve(noiseTexture);
  return new Promise((resolve, reject) => {
    noiseTexture.addEventListener("load", () => resolve(noiseTexture), { once: true });
    noiseTexture.addEventListener("error", () => reject(new Error("Noise texture unavailable")), { once: true });
  });
}

function sizingUniforms({ scale = 1, offsetX = 0, offsetY = 0 } = {}) {
  return {
    u_fit: ShaderFitOptions.none,
    u_scale: scale,
    u_rotation: 0,
    u_offsetX: offsetX,
    u_offsetY: offsetY,
    u_originX: 0.5,
    u_originY: 0.5,
    u_worldWidth: 0,
    u_worldHeight: 0,
  };
}

const DEFAULT_COLORS = {
  front: "rgba(126,243,255,0.62)",
  back: "rgba(214,92,255,0.22)",
  edge: ["rgba(157,139,255,0.72)", "rgba(199,125,255,0.48)"],
};

function themeColors(element) {
  const style = element && element.isConnected ? getComputedStyle(element) : null;
  const rgba = (name, alpha, fallback) => {
    const channels = style ? style.getPropertyValue(`--${name}-rgb`).trim().split(/\s+/) : [];
    return channels.length === 3 ? `rgba(${channels.join(",")},${alpha})` : fallback;
  };
  return {
    front: rgba("signal-hi", 0.62, DEFAULT_COLORS.front),
    back: rgba("aura-glow", 0.22, DEFAULT_COLORS.back),
    edge: [rgba("signal", 0.72, DEFAULT_COLORS.edge[0]), rgba("signal-mid", 0.48, DEFAULT_COLORS.edge[1])],
  };
}

function heroUniforms(element, presentation) {
  const colors = themeColors(element);
  const strength = Math.max(0.58, Math.min(1, Number(presentation.heroStrength) || 0.58));
  return {
    u_colorBack: getShaderColorFromString(colors.back),
    u_colorFront: getShaderColorFromString(colors.front),
    u_strength: strength,
    u_pxSize: 2.6,
  };
}

function borderColorUniforms(element) {
  return { u_colors: themeColors(element).edge.map(getShaderColorFromString), u_colorsCount: 2 };
}

function borderUniforms(texture, element) {
  return {
    u_noiseTexture: texture,
    u_colorBack: getShaderColorFromString("#00000000"),
    ...borderColorUniforms(element),
    u_roundness: 0.08,
    u_thickness: 0.013,
    u_marginLeft: 0.002,
    u_marginRight: 0.002,
    u_marginTop: 0.002,
    u_marginBottom: 0.002,
    u_aspectRatio: PulsingBorderAspectRatios.auto,
    u_softness: 0.48,
    u_intensity: 0.18,
    u_bloom: 0.06,
    u_spotSize: 0.66,
    u_spots: 1,
    u_pulse: 0.08,
    u_smoke: 0,
    u_smokeSize: 0.4,
    ...sizingUniforms(),
  };
}

function mountHandle(element, fragmentShader, uniforms, presentation, maxPixelCount) {
  const mount = new ShaderMount(
    element,
    fragmentShader,
    uniforms,
    WEBGL_OPTIONS,
    presentation.speed,
    presentation.frame,
    1,
    maxPixelCount,
  );
  element.dataset.pageEffect = "ready";
  return {
    setSpeed(speed) {
      mount.setSpeed(speed);
    },
    setUniforms(nextUniforms) {
      mount.setUniforms(nextUniforms);
    },
    dispose() {
      mount.dispose();
      delete element.dataset.pageEffect;
    },
  };
}

export async function mountDitherField(element, presentation) {
  const handle = mountHandle(
    element,
    orderedDitherRainFragmentShader,
    heroUniforms(element, presentation),
    { speed: presentation.heroSpeed, frame: presentation.heroFrame },
    720000,
  );
  return {
    update(next) {
      handle.setUniforms(heroUniforms(element, next));
      handle.setSpeed(next.heroSpeed);
    },
    dispose: handle.dispose,
  };
}

export async function mountPulsingBorder(element, presentation, isCurrent = () => true) {
  const texture = await readyNoiseTexture();
  if (!isCurrent()) return null;
  const handle = mountHandle(
    element,
    pulsingBorderFragmentShader,
    borderUniforms(texture, element),
    { speed: presentation.borderSpeed, frame: presentation.borderFrame },
    260000,
  );
  return {
    update(next) {
      handle.setUniforms(borderColorUniforms(element));
      handle.setSpeed(next.borderSpeed);
    },
    dispose: handle.dispose,
  };
}
