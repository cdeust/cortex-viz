// Cortex Methodology Map — Labels
window.CMV = window.CMV || {};

/**
 * Create a canvas texture with a flat, ink-coloured label + hairline
 * underline (no glow/bloom — design-system doctrine). Used for domain hub
 * labels rendered as THREE.Sprite.
 * @param {string} text - Label text to render.
 * @returns {THREE.CanvasTexture} Texture for sprite material.
 */
CMV.makeLabel = function (text) {
  var c = document.createElement('canvas');
  c.width = 512;
  c.height = 128;
  var ctx = c.getContext('2d');
  ctx.clearRect(0, 0, 512, 128);
  ctx.font = '600 26px "Inter Tight", sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  var hex = (window.CortexPalette && window.CortexPalette.hex) || function () { return '#E8F8FF'; };
  var ink = hex('--text');
  var rule = hex('--border-strong');

  ctx.fillStyle = ink;
  ctx.fillText(text.toUpperCase(), 256, 58);

  // Underline
  var w = ctx.measureText(text.toUpperCase()).width;
  ctx.strokeStyle = rule;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(256 - w / 2, 78);
  ctx.lineTo(256 + w / 2, 78);
  ctx.stroke();

  return new THREE.CanvasTexture(c);
};
