shader_type spatial;

// 圆形灵质台。
// 不用 UV，直接用世界坐标算极坐标（这样以后把台子摆到任何地方都不会错）：
// - r > radius 的片段直接 discard，所以一张方形的 PlaneMesh 看起来就是一个正圆，
//   恰好对上 CylinderShape3D 的碰撞体（半径 = radius）；
// - 同心细环用 fwidth 做抗锯齿；
// - 0.70R~0.84R 是一圈慢慢旋转的符文刻度；
// - 中心灵纹盘 + 外缘强灵光。

uniform float radius = 30.0;
uniform vec3 base_color : source_color = vec3(0.05, 0.07, 0.11);
uniform vec3 line_color : source_color = vec3(0.20, 0.55, 0.78);
uniform vec3 rune_color : source_color = vec3(0.55, 0.92, 1.0);
uniform float rune_count = 64.0;
uniform float spin_speed = 0.03;
uniform float emission_strength = 0.8;

varying vec3 g_world_pos;

void vertex() {
	g_world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	vec3 center = (MODEL_MATRIX * vec4(0.0, 0.0, 0.0, 1.0)).xyz;
	vec2 p = g_world_pos.xz - center.xz;
	float r = length(p);
	if (r > radius) {
		discard;
	}
	float a = atan(p.y, p.x);

	// 同心细环（每 2 米一环）
	float rings = abs(fract(r * 0.5) - 0.5) / max(fwidth(r * 0.5), 0.0001);
	float ring_line = 1.0 - min(rings, 1.0);

	// 旋转符环
	float band_mask = smoothstep(0.70, 0.74, r / radius) * (1.0 - smoothstep(0.80, 0.84, r / radius));
	float ticks = step(0.55, fract(a / TAU * rune_count + TIME * spin_speed));
	float rune_ring = band_mask * (0.35 + 0.65 * ticks);

	// 中心灵纹盘 + 外缘灵光
	float inner = 1.0 - smoothstep(0.0, 0.16, r / radius);
	float rim = smoothstep(0.965, 1.0, r / radius);

	float glow = ring_line * 0.35 + rune_ring + rim * 1.2 + inner * 0.5;

	vec3 col = mix(base_color, line_color, clamp(ring_line * 0.55 + rune_ring * 0.70, 0.0, 1.0));
	col = mix(col, rune_color, clamp(rim * 0.90 + inner * 0.35, 0.0, 1.0));

	ALBEDO = col;
	EMISSION = col * glow * emission_strength;
	ROUGHNESS = 0.85;
	METALLIC = 0.0;
}
