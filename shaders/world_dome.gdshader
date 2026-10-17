shader_type spatial;

// 灵质穹顶（包住整个世界的球，从里面看）。
// - cull_front：球内观看；
// - fog_disabled：天空不吃雾，否则星海会被雾抹平；
// - 用「方向」而不是 UV 算渐变与星点，这样无论 SphereMesh 的 UV 朝向如何都不会翻。
// 同一个 shader 两套参数：门外 star_amount=1（星海），门内 star_amount=0（云海辉光）。

render_mode unshaded, cull_front, fog_disabled;

uniform vec3 top_color : source_color = vec3(0.01, 0.03, 0.07);
uniform vec3 horizon_color : source_color = vec3(0.05, 0.12, 0.22);
uniform vec3 below_color : source_color = vec3(0.0, 0.0, 0.0);
uniform vec3 star_color : source_color = vec3(0.75, 0.85, 1.0);
uniform float star_amount = 1.0;
uniform float star_density = 260.0;

varying vec3 g_world_pos;

float hash31(vec3 p) {
	return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453123);
}

void vertex() {
	g_world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	vec3 center = (MODEL_MATRIX * vec4(0.0, 0.0, 0.0, 1.0)).xyz;
	vec3 d = normalize(g_world_pos - center);

	vec3 col = mix(horizon_color, top_color, pow(clamp(d.y, 0.0, 1.0), 0.65));
	col = mix(below_color, col, smoothstep(-0.30, 0.02, d.y));

	if (star_amount > 0.001) {
		vec3 g = d * star_density;
		vec3 cell = floor(g);
		vec3 f = fract(g) - 0.5;
		float h = hash31(cell);
		float dot_mask = smoothstep(0.18, 0.0, length(f));
		float bright = step(0.994, h) * (0.35 + 0.65 * fract(h * 37.0));
		float twinkle = 0.65 + 0.35 * sin(TIME * 1.7 + h * 90.0);
		col += star_color * dot_mask * bright * twinkle * star_amount;
	}

	ALBEDO = col;
}
