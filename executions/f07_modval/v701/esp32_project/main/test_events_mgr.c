// test_events_mgr.c
#include <stdio.h>
#include <stdlib.h>
#include "events_mgr.h"

static void print_array(event_t *arr, size_t len) {
    if (!arr) { 
        printf("(null)\n"); 
        return; 
    }
    printf("[");
    for (size_t i = 0; i < len; ++i) {
        printf("%d %s", arr[i], i + 1 < len ? ", " : "");
    }
    printf("]\n");
}

int main(void) {
    events_mgr_t *mgr = events_mgr_create();
    if (!mgr) {
        fprintf(stderr, "Failed to create events manager\n");
        return 1;
    }

    printf("TIME WINDOW = %d units\n\n", EVENTS_MGR_TIME_WINDOW);

    for (int t = 0; t <= 3000; t += 100) {
        size_t len = (t / 100) + 1;
        event_t *data = malloc(len * sizeof *data);
        for (size_t i = 0; i < len; ++i) 
            data[i] = t + (event_t)i;

        if (events_mgr_add(mgr, t, data, len) != 0) {
            fprintf(stderr, "Add failed at time %d\n", t);
            free(data);
            events_mgr_destroy(mgr);
            return 1;
        }
        free(data);

        printf("Added @%3d (len=%zu). Fetched: ", t, len);
        size_t outlen;
        event_t* peek = events_mgr_get_at(mgr, t, &outlen);
        print_array(peek, outlen);
        free(peek);
    }

    printf("\n-- After all adds, fetching old times --\n");
    for (int t = 0; t <= 3000; t += 100) {
        size_t outlen;
        event_t *got = events_mgr_get_at(mgr, t, &outlen);
        printf("Time %3d: ", t);
        print_array(got, outlen);
        free(got);
    }

    printf("\n-- Fetch range 300..1200 --\n");
    size_t rangelen;
    event_t *range = events_mgr_get_range(mgr, 300, 1200, &rangelen);
    printf("Concatenated [%zu elems]: ", rangelen);
    print_array(range, rangelen);
    free(range);

    events_mgr_destroy(mgr);
    return 0;
}
