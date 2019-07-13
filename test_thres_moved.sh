RED='\033[1;31m'
PURPLE='\033[1;35m'
RESET='\033[0m'
BEGIN=1
END=100
for ((i=$BEGIN; i<=$END; i++))
do
  read -p "[Set $(echo -e $RED"Prior"$RESET)] Press enter to continue..."
  rosservice call /get_reward/set_prior
  read -p "[Set $(echo -e $PURPLE"Posterior"$RESET)] Press enter to continue..."
  rosservice call /get_reward/set_posterior
  rosservice call /get_reward/get_result "data: false"
  echo "$i"
done
