shader_type canvas_item;

// 穿界白闪：白色从“远处”向“近处”刷新过去。
//
// 第一人称里，“远处”就是画面中心（地平线 / 光柱所在的方向），“近处”就是画面四周。
// 所以做法是在屏幕空间里量到中心的距离 d：
//   progress 从 0 涨到 1.6 的过程中，白色先从 d 小的地方（中心）开始铺，
//   再向外扩到 d 大的地方（四周）—— 观感就是“白光由远及近扑面而来”。
// 铺满整屏之后，再用 fade 整体淡出（此时已经完成传送，玩家看到的是目的地的画面）。

render_mode unshaded;

uniform vec4 wipe_color : source_color = vec4(1.0, 1.0, 1.0, 1.0);
// 0 = 完全透明；1.6 左右 = 盖满整屏
uniform float progress = 0.0;
// 锋面宽度（越大越柔）
uniform float softness = 0.42;
// 铺满之后的整体淡出系数
uniform float fade = 0.0;
// 屏幕宽高比（1600x900 -> 1.77778），把椭圆修正回正圆
uniform float aspect = 1.77778;

void fragment() {
	vec2 p = UV - vec2(0.5);
	p.x *= aspect;
	float d = length(p);
	float cover = smoothstep(progress - softness, progress, d);
	float a = (1.0 - cover) * fade * wipe_color.a;
	COLOR = vec4(wipe_color.rgb, clamp(a, 0.0, 1.0));
}
