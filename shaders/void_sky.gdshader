shader_type sky;

// 门外虚空的天空：地平线附近是深灰、越往上越浅。
// - 用 EYEDIR.y（视线方向的竖直分量）算渐变，不用 UV —— 平视时就是「屏幕下方黑、上方灰」；
// - fade_below 让渐变**跨过地平线**：从地平线下方就开始慢慢变亮，黑与灰之间没有明确的"起坡点"，
//   所以天与地之间看不出分界。把它设回 0，就退化成「地平线以下纯黑」的老样子；
// - 加一点亚像素抖动（dither），否则大面积平滑渐变在 8 bit 输出上会出现一圈圈横向色带。

render_mode disable_fog;

uniform vec3 bottom_color : source_color = vec3(0.0, 0.0, 0.0);
uniform vec3 top_color : source_color = vec3(0.36, 0.38, 0.42);
uniform float fade_height : hint_range(0.05, 1.5, 0.01) = 1.0;
uniform float fade_below : hint_range(0.0, 1.0, 0.01) = 0.3;
uniform float dither_amount : hint_range(0.0, 2.0, 0.05) = 1.0;

float hash13(vec3 p) {
	return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453123);
}

void sky() {
	float span = fade_height + fade_below;
	float t = clamp((EYEDIR.y + fade_below) / span, 0.0, 1.0);
	t = t * t * (3.0 - 2.0 * t);
	vec3 col = mix(bottom_color, top_color, t);

	// 抖动幅度取「显示空间 1 个色阶（8 bit）折算回线性亮度」的大小：
	// 线性上暗处小、亮处大，换算到显示空间就都是大约一级灰阶。
	float n = hash13(EYEDIR * 260.0) - 0.5;
	vec3 step_lin = (1.0 / 255.0) * 2.2 * sqrt(max(col, vec3(0.0)));
	COLOR = col + step_lin * (n * dither_amount);
}
