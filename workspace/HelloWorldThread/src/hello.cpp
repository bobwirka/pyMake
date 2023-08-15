#include    <unistd.h>
#include    <stdlib.h>
#include    <stdio.h>
#include    <stdint.h>

#include    <pthread.h>

#include    <mstime.h>


void* printThread(void* args)
{
    int         i;
    uint64_t    now;

    for (i = 0 ; i < 3 ; ++i)
    {
        now = msTime();
        printf("Hello World!:%lld\n", (long long int)now);
        msSleep(2000);
    }
    return NULL;
}

int main(int argc , char** argv)
{
    int         result;
    pthread_t   tid;

    printf("Starting thread...\n");
    result = pthread_create(&tid , 0 , printThread , NULL);
    if (result == 0)
    {
        pthread_join(tid , NULL);
        printf("Thread returned\n");
    }
    else
    {
        printf("Unable to start thread\n");
    }

    return 0;
}