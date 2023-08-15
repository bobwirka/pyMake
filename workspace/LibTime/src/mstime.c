/*
 * mod_time.c
 *
 *  Created on: Dec 10, 2022
 *      Author: rcw
 */

#include <unistd.h>
#include <time.h>

#include <mstime.h>

#define     ONE_MILLION     1000000L

/**
 * Returns system time in milliseconds since the
 * epoch 1970-01-01 00:00:00 +0000 (UTC).
 */
uint64_t msTime(void)
{
    struct timespec     ts;

    // Get time since epoch.
    if (clock_gettime(CLOCK_MONOTONIC , &ts))
        return 0;
    // Return with milliseconds.
    return (ts.tv_sec * 1000) + (ts.tv_nsec / ONE_MILLION);
}

void msSleep(uint32_t msTime)
{
    usleep(msTime * 1000);
}
