/* EA-SKILL 轻量 host 单测 runner（零依赖断言，gcc 直接编译运行） */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int __ea_pass = 0, __ea_fail = 0;

#define ASSERT_TRUE(cond) do { \
    if (cond) { __ea_pass++; } \
    else { __ea_fail++; printf("  x %s:%d ASSERT_TRUE(%s) failed\n", __FILE__, __LINE__, #cond); } \
} while (0)

#define ASSERT_EQ(a, b) do { \
    long long _a = (long long)(a), _b = (long long)(b); \
    if (_a == _b) { __ea_pass++; } \
    else { __ea_fail++; printf("  x %s:%d ASSERT_EQ(%s, %s) -> %lld != %lld\n", __FILE__, __LINE__, #a, #b, _a, _b); } \
} while (0)

#define ASSERT_STR_EQ(a, b) do { \
    if (strcmp((a), (b)) == 0) { __ea_pass++; } \
    else { __ea_fail++; printf("  x %s:%d ASSERT_STR_EQ(%s, %s) -> \"%s\" != \"%s\"\n", __FILE__, __LINE__, #a, #b, (a), (b)); } \
} while (0)

static void __ea_summary(const char *suite) {
    printf("====[%s] PASS=%d FAIL=%d\n", suite, __ea_pass, __ea_fail);
    printf("%s\n", __ea_fail == 0 ? "TEST PASS" : "TEST FAIL");
    exit(__ea_fail == 0 ? 0 : 1);
}

/* 硬件外设桩示例（按被测代码替换为真实桩） */
/* int mock_uart_read(void) { return 0; } */

/* 被测函数声明 */
/* #include "target.h" */

int main(void) {
    printf("[suite] <target>\n");
    /* 用例写在这里 */
    /* ASSERT_EQ(target_func(0), 0); */
    __ea_summary("<target>");
    return 0;
}
